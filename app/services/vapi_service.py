"""
FaceTracer Vapi AI Phone Calling Service
=========================================
Handles all communication with the Vapi REST API for outbound phone calls.

Features:
  - Start outbound AI-powered calls via Vapi
  - End active calls
  - Poll call status
  - Fetch call history from Vapi + MongoDB
  - Persist every call record to MongoDB (vapi_call_logs collection)
  - Phone number validation to E.164
  - Structured logging throughout
  - All credentials read from environment — never exposed to client
"""
import os
import re
import json
import logging
import datetime
import urllib.request
import urllib.error
from typing import Optional

from pymongo import MongoClient

logger = logging.getLogger(__name__)

# ── Env config ────────────────────────────────────────────────────────────────
VAPI_BASE_URL   = "https://api.vapi.ai"
VAPI_API_KEY    = lambda: os.getenv("VAPI_PRIVATE_API_KEY", os.getenv("VAPI_API_KEY", ""))
VAPI_ASST_ID    = lambda: os.getenv("VAPI_ASSISTANT_ID", "")
VAPI_PHONE_ID   = lambda: os.getenv("VAPI_PHONE_NUMBER_ID", "")
MONGODB_URI     = lambda: os.getenv("MONGODB_URI", "")


# ── DB helper ─────────────────────────────────────────────────────────────────
def _get_db():
    uri = MONGODB_URI()
    if not uri:
        return None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        try:
            return client.get_default_database()
        except Exception:
            return client["facetracer"]
    except Exception as exc:
        logger.error(f"[VapiService] DB connection failed: {exc}")
        return None


# ── Phone validator ───────────────────────────────────────────────────────────
def validate_e164(phone: str, default_cc: str = "+91") -> Optional[str]:
    """
    Validate and convert a phone number to E.164 format.
    Returns formatted number or None if invalid.
    """
    if not phone:
        return None
    raw = str(phone).strip()
    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)

    if len(digits) < 7 or len(digits) > 15:
        return None

    if has_plus:
        formatted = f"+{digits}"
    elif digits.startswith("91") and len(digits) == 12:
        formatted = f"+{digits}"
    elif digits.startswith("0") and len(digits) == 11:
        formatted = f"{default_cc}{digits[1:]}"
    elif len(digits) == 10:
        formatted = f"{default_cc}{digits}"
    else:
        formatted = f"+{digits}"

    if re.match(r"^\+[1-9]\d{6,14}$", formatted):
        return formatted

    logger.warning(f"[VapiService] Phone '{formatted}' failed E.164 check.")
    return None


# ── HTTP helper ───────────────────────────────────────────────────────────────
def _vapi_request(method: str, path: str, body: Optional[dict] = None) -> dict:
    """
    Make an authenticated request to the Vapi REST API.
    Returns { success, data, status_code, error }.
    """
    api_key = VAPI_API_KEY()
    if not api_key:
        return {
            "success": False,
            "data": None,
            "status_code": None,
            "error": "VAPI_PRIVATE_API_KEY is not set in environment.",
        }

    url = f"{VAPI_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "FaceTracer-Backend/2.0",
    }

    req_data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8")
            data = json.loads(resp_body) if resp_body.strip() else {}
            return {"success": True, "data": data, "status_code": resp.status, "error": None}
    except urllib.error.HTTPError as http_err:
        err_body = ""
        try:
            err_body = http_err.read().decode("utf-8")
        except Exception:
            pass
        logger.error(f"[VapiService] HTTP {http_err.code} on {method} {path}: {err_body}")
        return {
            "success": False,
            "data": None,
            "status_code": http_err.code,
            "error": f"Vapi API error {http_err.code}: {err_body}",
        }
    except Exception as exc:
        logger.error(f"[VapiService] Request exception on {method} {path}: {exc}", exc_info=True)
        return {"success": False, "data": None, "status_code": None, "error": str(exc)}


# ── Persistence helpers ───────────────────────────────────────────────────────
def _save_call_log(
    db,
    call_id: str,
    phone_number: str,
    user_id: str,
    case_id: Optional[str],
    status: str,
    vapi_response: Optional[dict] = None,
) -> None:
    """Insert a new call log document into MongoDB."""
    if db is None:
        return
    try:
        db["vapi_call_logs"].insert_one({
            "call_id":       call_id,
            "phone_number":  phone_number,
            "user_id":       user_id,
            "case_id":       case_id or "",
            "call_status":   status,
            "vapi_response": vapi_response or {},
            "start_time":    datetime.datetime.utcnow(),
            "end_time":      None,
            "created_at":    datetime.datetime.utcnow(),
        })
        logger.info(f"[VapiService] Call log saved | ID: {call_id} | Status: {status}")
    except Exception as exc:
        logger.error(f"[VapiService] Failed to save call log: {exc}")


def _update_call_log(db, call_id: str, updates: dict) -> None:
    """Update an existing call log by call_id."""
    if db is None:
        return
    try:
        db["vapi_call_logs"].update_one(
            {"call_id": call_id},
            {"$set": updates},
        )
    except Exception as exc:
        logger.error(f"[VapiService] Failed to update call log {call_id}: {exc}")


# ── Public service functions ───────────────────────────────────────────────────

def start_call(
    phone_number: str,
    user_id: str,
    case_id: Optional[str] = None,
    first_message: Optional[str] = None,
) -> dict:
    """
    Start an outbound AI call via Vapi.

    Args:
        phone_number: Recipient's phone number (will be validated to E.164).
        user_id:      ID of the authenticated user initiating the call.
        case_id:      Optional case reference ID to attach to the call log.
        first_message: Optional custom greeting override.

    Returns:
        dict: { success, call_id, status, phone_number, error }
    """
    logger.info(f"[VapiService] start_call | Phone: {phone_number} | User: {user_id} | Case: {case_id}")

    # ── Validate credentials ──────────────────────────────────────────────
    if not VAPI_API_KEY():
        return {"success": False, "call_id": None, "status": None,
                "error": "VAPI_PRIVATE_API_KEY not configured in .env"}
    if not VAPI_ASST_ID():
        return {"success": False, "call_id": None, "status": None,
                "error": "VAPI_ASSISTANT_ID not configured in .env"}
    if not VAPI_PHONE_ID():
        return {"success": False, "call_id": None, "status": None,
                "error": "VAPI_PHONE_NUMBER_ID not configured in .env"}

    # ── Validate phone number ─────────────────────────────────────────────
    formatted = validate_e164(phone_number)
    if not formatted:
        return {"success": False, "call_id": None, "status": None,
                "error": f"Invalid phone number '{phone_number}'. Provide a valid E.164 number (e.g. +919876543210)."}

    # ── Build Vapi payload ────────────────────────────────────────────────
    payload: dict = {
        "assistantId":   VAPI_ASST_ID(),
        "phoneNumberId": VAPI_PHONE_ID(),
        "customer": {
            "number": formatted,
        },
    }

    # Attach optional greeting override
    if first_message:
        payload["assistantOverrides"] = {"firstMessage": first_message}

    # Tag metadata for easy lookup in Vapi dashboard
    if case_id:
        payload["metadata"] = {"case_id": case_id, "initiated_by": user_id}

    # ── Call Vapi ─────────────────────────────────────────────────────────
    result = _vapi_request("POST", "/call", payload)

    if not result["success"]:
        logger.warning(f"[VapiService] start_call failed | Error: {result['error']}")
        return {
            "success": False,
            "call_id": None,
            "status":  None,
            "phone_number": formatted,
            "error":   result["error"],
        }

    data    = result["data"] or {}
    call_id = data.get("id") or data.get("callId") or ""
    status  = data.get("status", "queued")

    logger.info(f"[VapiService] Call started | Call ID: {call_id} | Status: {status}")

    # ── Persist to MongoDB ────────────────────────────────────────────────
    db = _get_db()
    _save_call_log(
        db=db,
        call_id=call_id,
        phone_number=formatted,
        user_id=user_id,
        case_id=case_id,
        status=status,
        vapi_response=data,
    )

    # ── Schedule status sync after 30s ────────────────────────────────────
    # Vapi transitions: queued → ringing → in-progress → ended
    # Sync real status back to DB so dashboard shows correct info
    if call_id:
        import threading
        def _sync_status(cid: str):
            import time
            time.sleep(30)
            try:
                r = _vapi_request("GET", f"/call/{cid}")
                if r["success"]:
                    real = r["data"].get("status", "unknown")
                    reason = r["data"].get("endedReason", "")
                    sdb = _get_db()
                    _update_call_log(sdb, cid, {
                        "call_status":  real,
                        "ended_reason": reason,
                        "end_time": datetime.datetime.utcnow() if real == "ended" else None,
                    })
                    logger.info(f"[VapiService] Synced {cid[:16]}: {status} → {real} ({reason})")
            except Exception as e:
                logger.warning(f"[VapiService] Sync failed {cid}: {e}")
        threading.Thread(target=_sync_status, args=(call_id,), daemon=True).start()

    return {
        "success":      True,
        "call_id":      call_id,
        "status":       status,
        "phone_number": formatted,
        "error":        None,
    }
def end_call(call_id: str, user_id: str) -> dict:
    """
    End an active Vapi call by call ID.

    Args:
        call_id: The Vapi call ID returned from start_call.
        user_id: ID of the user ending the call (for audit).

    Returns:
        dict: { success, call_id, status, error }
    """
    logger.info(f"[VapiService] end_call | Call ID: {call_id} | User: {user_id}")

    if not call_id or not call_id.strip():
        return {"success": False, "call_id": call_id, "status": None,
                "error": "call_id is required."}

    result = _vapi_request("DELETE", f"/call/{call_id.strip()}")

    if not result["success"]:
        # 404 means the call already ended naturally — treat as success
        if result.get("status_code") == 404:
            logger.info(f"[VapiService] Call {call_id} already ended (404).")
            db = _get_db()
            _update_call_log(db, call_id, {
                "call_status": "ended",
                "end_time":    datetime.datetime.utcnow(),
            })
            return {"success": True, "call_id": call_id, "status": "ended", "error": None}

        logger.warning(f"[VapiService] end_call failed | Error: {result['error']}")
        return {"success": False, "call_id": call_id, "status": None, "error": result["error"]}

    db = _get_db()
    _update_call_log(db, call_id, {
        "call_status": "ended",
        "end_time":    datetime.datetime.utcnow(),
    })

    logger.info(f"[VapiService] Call ended | Call ID: {call_id}")
    return {"success": True, "call_id": call_id, "status": "ended", "error": None}


def get_call_status(call_id: str) -> dict:
    """
    Fetch the current status of a Vapi call.

    Args:
        call_id: The Vapi call ID.

    Returns:
        dict: { success, call_id, status, duration, ended_reason, error }
    """
    if not call_id or not call_id.strip():
        return {"success": False, "call_id": call_id, "status": None, "error": "call_id is required."}

    result = _vapi_request("GET", f"/call/{call_id.strip()}")

    if not result["success"]:
        return {
            "success": False, "call_id": call_id, "status": None,
            "duration": None, "ended_reason": None, "error": result["error"],
        }

    data   = result["data"] or {}
    status = data.get("status", "unknown")
    ended_reason = data.get("endedReason")
    duration = None

    # Compute duration from timestamps if available
    started_at  = data.get("startedAt")
    ended_at    = data.get("endedAt")
    if started_at and ended_at:
        try:
            from dateutil import parser as dtparser
            start_dt = dtparser.parse(started_at)
            end_dt   = dtparser.parse(ended_at)
            duration = round((end_dt - start_dt).total_seconds())
        except ImportError:
            # python-dateutil not installed — attempt stdlib fallback
            try:
                import datetime as _dt
                fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
                start_dt = _dt.datetime.strptime(started_at[:26].rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f")
                end_dt   = _dt.datetime.strptime(ended_at[:26].rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f")
                duration = round((end_dt - start_dt).total_seconds())
            except Exception:
                pass
        except Exception:
            pass

    # Sync status to DB
    db = _get_db()
    if db is not None:
        update: dict = {"call_status": status}
        if ended_at:
            update["end_time"] = datetime.datetime.utcnow()
        _update_call_log(db, call_id, update)

    return {
        "success":      True,
        "call_id":      call_id,
        "status":       status,
        "duration":     duration,
        "ended_reason": ended_reason,
        "error":        None,
        "raw":          data,
    }


def get_call_history(user_id: str, limit: int = 20) -> list:
    """
    Fetch recent call history for a user from MongoDB.

    Args:
        user_id: Filter logs to this user.
        limit:   Maximum number of records to return (default 20, max 100).

    Returns:
        List of call log dicts.
    """
    db = _get_db()
    if db is None:
        return []

    try:
        logs = list(
            db["vapi_call_logs"]
            .find({"user_id": user_id})
            .sort("created_at", -1)
            .limit(min(limit, 100))
        )
        result = []
        for log in logs:
            log["id"] = str(log.pop("_id"))
            # Serialize datetimes
            for key in ("start_time", "end_time", "created_at"):
                if isinstance(log.get(key), datetime.datetime):
                    log[key] = log[key].isoformat()
            result.append(log)
        return result
    except Exception as exc:
        logger.error(f"[VapiService] get_call_history failed: {exc}")
        return []
