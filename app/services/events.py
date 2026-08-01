"""
FaceTracer Case Resolution Event Dispatcher
============================================
Central event system for case status changes.

Usage (from any router):
    from ..services.events import dispatch_case_resolved
    background_tasks.add_task(
        dispatch_case_resolved,
        case=person_doc,
        new_status="Resolved",
        db_uri=os.getenv("MONGODB_URI",""),
        resolved_by_user_id=current_user["id"],
    )

All side-effects of a case resolution are handled here:
  1. AI Voice Call via Vapi (primary) with Twilio fallback
  2. (Future) Email notification
  3. (Future) Push notification
  4. (Future) Webhook
"""
import os
import logging
import datetime
from typing import Optional
from pymongo import MongoClient
from bson import ObjectId

from .language_service import normalize_language

logger = logging.getLogger(__name__)

RESOLVED_STATUSES = {"resolved", "solved", "found", "traced"}


# ── DB helper ─────────────────────────────────────────────────────────────────
def _get_db(db_uri: str):
    client = MongoClient(db_uri, serverSelectionTimeoutMS=5000)
    try:
        return client.get_default_database()
    except Exception:
        return client["facetracer"]


# ── Officer language lookup ───────────────────────────────────────────────────
def _get_officer_language(db, registered_by: Optional[str]) -> str:
    """Look up the registering officer's preferred language (EN / HI / MR)."""
    if not registered_by:
        return "EN"
    try:
        officer = db["users"].find_one({"_id": ObjectId(registered_by)})
        if officer:
            lang = (officer.get("preferred_language") or "EN").upper()
            return lang if lang in ("EN", "HI", "MR") else "EN"
    except Exception:
        pass
    return "EN"


# ── Phone resolver ────────────────────────────────────────────────────────────
def _resolve_phone(case: dict, db) -> Optional[str]:
    """
    Priority 1: contact_phone field on the case document.
    Priority 2: registering officer's profile phone number.
    Always returns E.164-formatted phone or None.
    """
    from .vapi_service import validate_e164

    def _fmt(raw: str) -> Optional[str]:
        raw = raw.strip()
        if not raw or len(raw) < 7:
            return None
        return validate_e164(raw)

    # Priority 1: case contact_phone
    phone = _fmt(case.get("contact_phone") or "")
    if phone:
        return phone

    # Priority 2: officer's phone from users collection
    registered_by = case.get("created_by")
    if registered_by and db is not None:
        try:
            officer = db["users"].find_one({"_id": ObjectId(registered_by)})
            if officer:
                phone = _fmt(officer.get("phone") or "")
                if phone:
                    return phone
        except Exception:
            pass

    return None


# ── Multilingual first-message builder ───────────────────────────────────────
def _build_first_message(person_name: str, case_id: str, language: str) -> str:
    """
    Build a natural, multilingual opening message for the Vapi AI assistant
    to speak when the call connects.

    Args:
        person_name: Name of the resolved missing person.
        case_id:     MongoDB ObjectId string (last 6 chars used as reference).
        language:    "EN" / "HI" / "MR"

    Returns:
        A plain-text greeting string for Vapi's firstMessage override.
    """
    lang = normalize_language(language)
    ref  = str(case_id)[-6:].upper()

    if lang == "HI":
        return (
            f"नमस्ते। "
            f"मैं FaceTracer AI Assistant बोल रहा हूँ। "
            f"आपको यह जानकर खुशी होगी कि "
            f"गुमशुदा व्यक्ति {person_name} का केस सफलतापूर्वक हल हो गया है। "
            f"केस संदर्भ संख्या है {ref}। "
            f"कृपया अधिक जानकारी के लिए FaceTracer Portal में लॉगिन करें। "
            f"धन्यवाद।"
        )
    elif lang == "MR":
        return (
            f"नमस्कार। "
            f"मी FaceTracer AI Assistant बोलत आहे। "
            f"तुम्हाला हे सांगताना आनंद होतो की "
            f"हरवलेली व्यक्ती {person_name} यांचे प्रकरण यशस्वीरित्या सोडवले गेले आहे। "
            f"प्रकरण संदर्भ क्रमांक {ref} आहे। "
            f"कृपया अधिक माहितीसाठी FaceTracer Portal मध्ये लॉगिन करा। "
            f"धन्यवाद।"
        )
    else:  # EN (default)
        return (
            f"Hello. "
            f"This is FaceTracer AI Assistant. "
            f"We are happy to inform you that the missing person case for {person_name} "
            f"has been successfully resolved. "
            f"Your case reference number is {ref}. "
            f"Please login to the FaceTracer Portal for complete details. "
            f"Thank you."
        )


# ── Duplicate call prevention ─────────────────────────────────────────────────
def _already_called(db, case_id: str) -> bool:
    """
    Check both vapi_call_logs and legacy call_logs to prevent duplicate calls
    for the same case resolution.
    """
    if db is None:
        return False
    case_id_str = str(case_id)

    # Check new Vapi call logs
    vapi_existing = db["vapi_call_logs"].find_one({
        "case_id":     case_id_str,
        "call_status": {"$in": ["queued", "ringing", "in-progress", "ended", "success"]},
    })
    if vapi_existing:
        return True

    # Check legacy call_logs (Twilio)
    legacy_existing = db["call_logs"].find_one({
        "case_id":     case_id_str,
        "call_status": "success",
    })
    return bool(legacy_existing)


# ── Master dispatcher ─────────────────────────────────────────────────────────
def dispatch_case_resolved(
    case:                dict,
    new_status:          str,
    db_uri:              str,
    resolved_by_user_id: Optional[str] = None,
    confidence:          Optional[float] = None,
) -> None:
    """
    Called as a background task whenever a missing person case transitions
    to a resolved status (Resolved / Found / Solved / Traced).

    Flow:
      1. Validate resolution status
      2. Connect to MongoDB
      3. Duplicate prevention check
      4. Resolve contact phone number
      5. Determine language preference
      6. Build multilingual first message
      7. Fire Vapi AI call (primary)
         → On failure, fall back to legacy Twilio/ai_calling_service
      8. Log result

    Never raises — all errors are caught and logged.
    """
    case_id     = str(case.get("_id", ""))
    person_name = case.get("name", "the missing person")
    old_status  = (case.get("status") or "Active")

    logger.info("=" * 60)
    logger.info(f"[Event] dispatch_case_resolved TRIGGERED")
    logger.info(f"[Event] Case: {case_id} | Person: {person_name}")
    logger.info(f"[Event] Status: {old_status} → {new_status} | Confidence: {confidence or 'N/A'}")
    logger.info("=" * 60)

    # ── Guard: only trigger for genuine resolution statuses ───────────────
    if new_status.lower() not in RESOLVED_STATUSES:
        logger.warning(
            f"[Event] '{new_status}' is not a resolution status. "
            f"Allowed: {RESOLVED_STATUSES}. Skipping."
        )
        return

    # ── Connect to MongoDB ────────────────────────────────────────────────
    db = None
    try:
        db = _get_db(db_uri)
        logger.info(f"[Event] DB connected: {db.name}")
    except Exception as exc:
        logger.error(f"[Event] DB connection failed: {exc}. Proceeding with limited functionality.")

    # ── Duplicate prevention ──────────────────────────────────────────────
    if db is not None and _already_called(db, case_id):
        logger.info(
            f"[Event] Case {case_id} already has a call record. "
            f"Skipping duplicate dispatch."
        )
        return

    # ── Resolve phone number ──────────────────────────────────────────────
    phone = _resolve_phone(case, db) if db else (case.get("contact_phone") or "").strip() or None

    if not phone:
        logger.warning(
            f"[Event] No contact phone for case {case_id} ({person_name}). "
            f"AI call skipped. Add contact_phone when registering the case."
        )
        return

    logger.info(f"[Event] Contact phone resolved: {phone}")

    # ── Determine language ────────────────────────────────────────────────
    registered_by = case.get("created_by")
    language = _get_officer_language(db, registered_by) if db else "EN"
    logger.info(f"[Event] Language: {language}")

    # ── Build first message ───────────────────────────────────────────────
    first_message = _build_first_message(person_name, case_id, language)
    logger.info(f"[Event] First message built ({language}): {first_message[:80]}…")

    # ── 1. Try Vapi AI call (primary) ─────────────────────────────────────
    vapi_success = False
    try:
        from .vapi_service import start_call as vapi_start_call

        logger.info(f"[Event] Placing Vapi call to {phone}…")
        result = vapi_start_call(
            phone_number=phone,
            user_id=resolved_by_user_id or "system",
            case_id=case_id,
            first_message=first_message,
        )

        if result.get("success"):
            vapi_success = True
            logger.info(
                f"[Event] ✅ Vapi call SUCCESS | "
                f"Call ID: {result.get('call_id')} | "
                f"Status: {result.get('status')} | "
                f"Phone: {phone}"
            )
        else:
            logger.warning(
                f"[Event] Vapi call failed | Reason: {result.get('error')} | "
                f"Falling back to legacy service…"
            )

    except Exception as exc:
        logger.error(f"[Event] Vapi call exception for case {case_id}: {exc}", exc_info=True)

    # ── 2. Fallback: legacy ai_calling_service (Twilio) ───────────────────
    if not vapi_success:
        logger.info(f"[Event] Attempting legacy AI call service (Twilio fallback)…")
        try:
            from .ai_calling_service import trigger_case_resolved_call
            legacy_result = trigger_case_resolved_call(
                to_phone=phone,
                person_name=person_name,
                case_id=case_id,
                case_status=new_status,
                language=language,
                db_uri=db_uri,
            )
            if legacy_result.get("success"):
                logger.info(
                    f"[Event] ✅ Legacy call SUCCESS | "
                    f"SID: {legacy_result.get('sid')} | "
                    f"Provider: {legacy_result.get('provider')}"
                )
            else:
                logger.error(
                    f"[Event] ❌ Legacy call also failed | "
                    f"Reason: {legacy_result.get('error')}"
                )
        except Exception as exc:
            logger.error(f"[Event] Legacy call exception for case {case_id}: {exc}", exc_info=True)

    # ── 3. Future: Email notification ─────────────────────────────────────
    # try:
    #     from .email_service import send_resolution_email
    #     send_resolution_email(case, new_status)
    # except Exception as exc:
    #     logger.error(f"[Event] Email failed: {exc}")

    logger.info(f"[Event] dispatch_case_resolved complete for case {case_id}")
