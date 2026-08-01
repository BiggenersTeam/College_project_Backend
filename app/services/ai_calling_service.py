"""
FaceTracer AI Voice Calling Assistant Service
=============================================
Production-ready, modular voice call service with dual provider support (Twilio & Vapi).

Features:
  - Automatic E.164 phone number formatting
  - Dual Provider support: Twilio Voice API & Vapi AI Voice API
  - Non-blocking execution via BackgroundTasks / Threading
  - Duplicate prevention via MongoDB call_logs
  - 3-attempt retry mechanism with backoff
  - Structured, detailed step logging and robust exception reporting
  - Multilingual support: EN / HI / MR
"""
import os
import re
import time
import logging
import datetime
import json
import urllib.request
import urllib.error
from typing import Optional

from pymongo import MongoClient

from .language_service import normalize_language, build_twiml_message

logger = logging.getLogger(__name__)

# ── Config from environment ───────────────────────────────────────────────────
VOICE_PROVIDER = os.getenv("VOICE_PROVIDER", "auto").lower()
CALLS_ENABLED  = os.getenv("VOICE_CALLS_ENABLED", "true").lower() == "true"
MAX_RETRIES    = 3
RETRY_DELAY_S  = 2   # seconds between retries

RESOLVED_STATUSES = {"resolved", "solved", "found", "traced"}


# ── E.164 Phone Number Validator & Formatter ──────────────────────────────────
def format_to_e164(phone_str: str, default_country_code: str = "+91") -> Optional[str]:
    """
    Validate and convert raw phone numbers to standard E.164 format.

    Examples:
      "919209341992"     → "+919209341992"
      "9209341992"       → "+919209341992"
      "09209341992"      → "+919209341992"
      "+91 92093-41992"  → "+919209341992"
      "+1 (555) 000-123" → "+1555000123"
    """
    if not phone_str:
        return None

    raw = str(phone_str).strip()
    # Check if original had leading '+'
    has_plus = raw.startswith("+")

    # Strip all non-digit characters
    digits = re.sub(r"\D", "", raw)

    if not digits or len(digits) < 7:
        return None

    # Handle standard patterns
    if has_plus:
        formatted = f"+{digits}"
    elif digits.startswith("91") and len(digits) == 12:
        formatted = f"+{digits}"
    elif digits.startswith("0") and len(digits) == 11:
        formatted = f"{default_country_code}{digits[1:]}"
    elif len(digits) == 10:
        formatted = f"{default_country_code}{digits}"
    else:
        # Fallback prepend default country code if missing plus
        formatted = f"+{digits}" if not digits.startswith(default_country_code.lstrip("+")) else f"+{digits}"

    # E.164 regex check: + followed by 7 to 15 digits
    if re.match(r"^\+[1-9]\d{6,14}$", formatted):
        return formatted
    
    logger.warning(f"[Phone Validation] Formatted number '{formatted}' failed E.164 regex check.")
    return formatted  # return best attempt


# ── DB helper ─────────────────────────────────────────────────────────────────
def _get_db(db_uri: str):
    """
    Connect to MongoDB and return default/facetracer database.
    """
    client = MongoClient(db_uri, serverSelectionTimeoutMS=5000)
    try:
        db = client.get_default_database()
    except Exception:
        db = client["facetracer"]
    return db


# ── Vapi AI Call Helper ───────────────────────────────────────────────────────
def _place_vapi_call(to_phone: str, person_name: str, case_id: str) -> dict:
    """
    Place a voice call via Vapi AI Voice API (api.vapi.ai).
    Uses VAPI_API_KEY, VAPI_ASSISTANT_ID, VAPI_PHONE_NUMBER_ID (optional).
    """
    api_key      = os.getenv("VAPI_API_KEY", "")
    assistant_id = os.getenv("VAPI_ASSISTANT_ID", "")
    phone_id     = os.getenv("VAPI_PHONE_NUMBER_ID", "")

    if not api_key or not assistant_id:
        return {
            "success": False, "sid": None, "status": None, "provider": "vapi",
            "error": "Vapi credentials missing. Set VAPI_API_KEY and VAPI_ASSISTANT_ID in .env"
        }

    # phoneNumberId is required for outbound calls via Vapi
    if not phone_id:
        return {
            "success": False, "sid": None, "status": None, "provider": "vapi",
            "error": (
                "VAPI_PHONE_NUMBER_ID not set in .env. "
                "Go to https://dashboard.vapi.ai → Phone Numbers → copy the Phone Number ID and add it to .env"
            )
        }

    url = "https://api.vapi.ai/call"
    payload = {
        "assistantId": assistant_id,
        "phoneNumberId": phone_id,
        "customer": {
            "number": to_phone
        },
        "assistantOverrides": {
            "firstMessage": (
                "Hello. "
                "This is FaceTracer AI Assistant. "
                "We are happy to inform you that the missing person case you registered has been successfully resolved. "
                "Please login to the FaceTracer Portal for complete details. "
                "Thank you."
            )
        }
    }

    req_data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "FaceTracer-AI-Call-Service/2.0"
    }

    try:
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8")
            data = json.loads(resp_body)
            call_id = data.get("id") or data.get("callId")
            status = data.get("status", "queued")
            logger.info(f"[Vapi Call] Request successful | Call ID: {call_id} | Status: {status}")
            return {"success": True, "sid": call_id, "status": status, "provider": "vapi", "error": None, "response": data}
    except urllib.error.HTTPError as http_err:
        err_body = http_err.read().decode("utf-8") if http_err.fp else str(http_err)
        logger.error(f"[Vapi Call HTTP Error] Status: {http_err.code} | Reason: {err_body}")
        return {"success": False, "sid": None, "status": f"HTTP {http_err.code}", "provider": "vapi", "error": f"HTTP {http_err.code}: {err_body}"}
    except Exception as exc:
        logger.error(f"[Vapi Call Exception] {exc}", exc_info=True)
        return {"success": False, "sid": None, "status": "failed", "provider": "vapi", "error": str(exc)}


# ── Twilio Call Helper ────────────────────────────────────────────────────────
def _place_twilio_call(to_phone: str, twiml: str) -> dict:
    """
    Place a voice call via Twilio.
    Returns: { success, sid, status, provider, error }
    """
    sid   = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    from_ = os.getenv("TWILIO_PHONE_NUMBER", "")

    if not all([sid, token, from_]):
        return {
            "success": False, "sid": None, "status": None, "provider": "twilio",
            "error": (
                "Twilio credentials not configured. "
                "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER in .env"
            ),
        }
    try:
        from twilio.rest import Client
        client = Client(sid, token)
        call = client.calls.create(
            twiml=twiml,
            to=to_phone.strip(),
            from_=from_.strip(),
        )
        logger.info(f"[Twilio Call] Call created successfully | Call SID: {call.sid} | Status: {call.status}")
        return {"success": True, "sid": call.sid, "status": call.status, "provider": "twilio", "error": None}
    except ImportError:
        err_msg = "twilio package not installed in environment. Run: pip install twilio"
        logger.error(f"[Twilio Call] {err_msg}")
        return {"success": False, "sid": None, "status": None, "provider": "twilio", "error": err_msg}
    except Exception as exc:
        logger.error(f"[Twilio Call Exception] {exc}", exc_info=True)
        return {"success": False, "sid": None, "status": None, "provider": "twilio", "error": str(exc)}


# ── Provider Router ───────────────────────────────────────────────────────────
def _place_call(to_phone: str, person_name: str, case_id: str, twiml: str, provider: str) -> dict:
    vapi_key = os.getenv("VAPI_API_KEY", "")
    vapi_assistant = os.getenv("VAPI_ASSISTANT_ID", "")
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID", "")

    selected_provider = provider.lower()

    if selected_provider == "vapi":
        return _place_vapi_call(to_phone, person_name, case_id)
    elif selected_provider == "twilio":
        return _place_twilio_call(to_phone, twiml)
    else:  # "auto" mode
        # If Vapi credentials present, try Vapi first, fallback to Twilio
        if vapi_key and vapi_assistant:
            logger.info("[Provider Router] Vapi credentials detected. Attempting Vapi call...")
            vapi_res = _place_vapi_call(to_phone, person_name, case_id)
            if vapi_res["success"]:
                return vapi_res
            logger.warning(f"[Provider Router] Vapi call attempt failed: {vapi_res.get('error')}. Falling back to Twilio...")

        # Default / Fallback to Twilio
        if twilio_sid:
            logger.info("[Provider Router] Attempting Twilio call...")
            return _place_twilio_call(to_phone, twiml)

        return {
            "success": False, "sid": None, "status": None, "provider": "none",
            "error": "No voice providers configured. Add Twilio or Vapi environment variables to .env"
        }


# ── Duplicate Prevention ──────────────────────────────────────────────────────
def _already_called_successfully(db, case_id: str) -> bool:
    if db is None:
        return False
    existing = db["call_logs"].find_one({
        "case_id":     str(case_id),
        "call_status": "success",
    })
    return bool(existing)


# ── Structured Call Logging ───────────────────────────────────────────────────
def _log_call(
    db,
    case_id:        str,
    raw_phone:      str,
    formatted_phone: str,
    result:         dict,
    attempt:        int,
    language:       str,
    provider:       str,
) -> None:
    if db is None:
        return
    try:
        db["call_logs"].insert_one({
            "case_id":         str(case_id),
            "phone_number":    formatted_phone or raw_phone,
            "raw_phone":        raw_phone,
            "call_status":     "success" if result.get("success") else "failed",
            "call_sid":        result.get("sid"),
            "call_provider":   result.get("provider", provider),
            "call_response":   result.get("status", ""),
            "error_message":   result.get("error"),
            "attempt":         attempt,
            "language":        language,
            "call_time":       datetime.datetime.utcnow(),
        })
        logger.info(f"[CallLog DB] Written log for case {case_id} (Attempt {attempt}, Status: {'success' if result.get('success') else 'failed'})")
    except Exception as exc:
        logger.error(f"[CallLog DB Exception] Failed to write call log for case {case_id}: {exc}")


# ── Public Entry Point ────────────────────────────────────────────────────────
def trigger_case_resolved_call(
    to_phone:    str,
    person_name: str,
    case_id:     str,
    case_status: str = "Resolved",
    language:    str = "EN",
    db_uri:      Optional[str] = None,
) -> dict:
    """
    Master function to trigger an AI voice call when a missing person case is resolved.

    Args:
        to_phone:    Phone number from case registration (contact_phone) or admin user
        person_name: Name of the missing person
        case_id:     MongoDB _id string of the case
        case_status: "Resolved" / "Found" / "Solved"
        language:    "EN" / "HI" / "MR"
        db_uri:      MongoDB URI (read from env if not provided)

    Returns:
        dict: { success, sid, status, provider, error }
    """
    logger.info("=" * 60)
    logger.info(f"[AI Voice Call Workflow] TRIGGERED for Case ID: {case_id}")
    logger.info(f"[AI Voice Call Workflow] Person Name: {person_name}")
    logger.info(f"[AI Voice Call Workflow] Registered Phone Raw: '{to_phone}'")
    logger.info("=" * 60)

    # ── Guard 1: Global Kill-Switch ───────────────────────────────────────
    if not CALLS_ENABLED:
        logger.info(f"[AI Call] Calls disabled (VOICE_CALLS_ENABLED=false). Skipping case {case_id}.")
        return {"success": False, "sid": None, "status": "disabled", "provider": VOICE_PROVIDER, "error": "Voice calls disabled via environment config."}

    # ── Guard 2: Validate & Format Phone Number to E.164 ──────────────────
    if not to_phone or not str(to_phone).strip():
        err_msg = f"Missing contact phone number for case {case_id}. Add contact_phone when registering the case."
        logger.warning(f"[AI Call] {err_msg}")
        return {"success": False, "sid": None, "status": "invalid_phone", "provider": VOICE_PROVIDER, "error": err_msg}

    formatted_phone = format_to_e164(to_phone)
    if not formatted_phone:
        err_msg = f"Phone number '{to_phone}' could not be converted to E.164 format for case {case_id}."
        logger.warning(f"[AI Call] {err_msg}")
        return {"success": False, "sid": None, "status": "invalid_phone", "provider": VOICE_PROVIDER, "error": err_msg}

    logger.info(f"[AI Call] Phone E.164 Formatted: {formatted_phone}")

    # ── Connect to DB for Duplicate Check and Logging ─────────────────────
    db = None
    uri = db_uri or os.getenv("MONGODB_URI", "")
    if uri:
        try:
            db = _get_db(uri)
        except Exception as exc:
            logger.warning(f"[AI Call] DB connection error: {exc}. Proceeding without DB logging.")

    # ── Guard 3: Duplicate Call Prevention ────────────────────────────────
    if db is not None and _already_called_successfully(db, case_id):
        logger.info(f"[AI Call] Case {case_id} has ALREADY received a successful voice call. Skipping duplicate call.")
        return {"success": False, "sid": None, "status": "duplicate_skipped", "provider": VOICE_PROVIDER, "error": "Duplicate — call already placed successfully."}

    # ── Build TwiML / Message ─────────────────────────────────────────────
    lang  = normalize_language(language)
    twiml = build_twiml_message(person_name, case_id, case_status, lang)
    prov  = VOICE_PROVIDER

    logger.info(f"[AI Call] Language: {lang} | Requested Provider: {prov}")
    logger.info(f"[AI Call] Initiating call execution loop (Max Retries: {MAX_RETRIES})...")

    # ── 3-Attempt Retry Loop with Backoff ──────────────────────────────────
    last_result: dict = {"success": False, "sid": None, "status": None, "provider": prov, "error": "No attempt made."}

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"[AI Call] Calling Provider (Attempt {attempt}/{MAX_RETRIES}) | Phone: {formatted_phone}...")
        
        result = _place_call(formatted_phone, person_name, case_id, twiml, prov)
        last_result = result

        # Log every attempt to MongoDB
        if db is not None:
            _log_call(db, case_id, str(to_phone), formatted_phone, result, attempt, lang, prov)

        if result.get("success"):
            logger.info("=" * 60)
            logger.info(f"✅ [AI Call SUCCESS] Attempt: {attempt}/{MAX_RETRIES}")
            logger.info(f"   Case ID:      {case_id}")
            logger.info(f"   To Phone:     {formatted_phone}")
            logger.info(f"   Provider:     {result.get('provider')}")
            logger.info(f"   Call SID/ID:  {result.get('sid')}")
            logger.info(f"   Call Status:  {result.get('status')}")
            logger.info("=" * 60)
            return result

        logger.warning(
            f"[AI Call FAILURE] Attempt {attempt}/{MAX_RETRIES} failed for case {case_id} | "
            f"Reason: {result.get('error')} | "
            f"{'Retrying in ' + str(RETRY_DELAY_S * attempt) + 's...' if attempt < MAX_RETRIES else 'Max retries exhausted.'}"
        )

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_S * attempt)  # 2s, 4s

    logger.error("=" * 60)
    logger.error(f"❌ [AI Call FAILED ALL RETRIES] Case ID: {case_id}")
    logger.error(f"   To Phone:     {formatted_phone}")
    logger.error(f"   Last Error:   {last_result.get('error')}")
    logger.error("=" * 60)

    return last_result

