"""
Call Logs API Router
Provides endpoints for reading call history and notification settings.
Only accessible by authenticated investigators.
"""
import os
import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pymongo.database import Database
from ..database import get_db
from ..models import doc_to_dict
from ..auth import get_current_user

router = APIRouter(prefix="/calls", tags=["AI Voice Calls"])


# ── GET /calls/logs — list all call logs ──────────────────────────────────────
@router.get("/logs")
def get_call_logs(
    case_id: Optional[str] = None,
    call_status: Optional[str] = None,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """
    Return call history. Filterable by case_id and call_status.
    """
    query: dict = {}
    if case_id:
        query["case_id"] = case_id
    if call_status:
        query["call_status"] = call_status

    logs = list(
        db["call_logs"]
        .find(query)
        .sort("call_time", -1)
        .limit(min(limit, 200))
    )
    result = []
    for log in logs:
        d = doc_to_dict(log)
        # Format call_time as ISO string
        if isinstance(d.get("call_time"), datetime.datetime):
            d["call_time"] = d["call_time"].isoformat()
        result.append(d)
    return result


# ── GET /calls/settings — get notification settings for current user ───────────
@router.get("/settings")
def get_notification_settings(
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Return current user's notification settings."""
    settings = db["notification_settings"].find_one(
        {"user_id": current_user["id"]}
    )
    if not settings:
        # Return defaults
        return {
            "user_id": current_user["id"],
            "voice_calls_enabled": True,
            "preferred_language": "EN",
        }
    return doc_to_dict(settings)


# ── PUT /calls/settings — update notification settings ────────────────────────
@router.put("/settings")
def update_notification_settings(
    payload: dict = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """
    Update notification settings for the current user.
    Payload: { voice_calls_enabled: bool, preferred_language: "EN"|"HI"|"MR" }
    """
    allowed_langs = {"EN", "HI", "MR"}
    voice_calls_enabled = payload.get("voice_calls_enabled", True)
    preferred_language = payload.get("preferred_language", "EN").upper()

    if preferred_language not in allowed_langs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid language. Allowed: {', '.join(allowed_langs)}"
        )

    db["notification_settings"].update_one(
        {"user_id": current_user["id"]},
        {
            "$set": {
                "user_id": current_user["id"],
                "voice_calls_enabled": bool(voice_calls_enabled),
                "preferred_language": preferred_language,
                "updated_at": datetime.datetime.utcnow(),
            }
        },
        upsert=True,
    )

    # Also update user's preferred_language in users collection
    from ..models import str_to_objectid
    try:
        db["users"].update_one(
            {"_id": str_to_objectid(current_user["id"])},
            {"$set": {"preferred_language": preferred_language}}
        )
    except Exception:
        pass

    return {
        "success": True,
        "user_id": current_user["id"],
        "voice_calls_enabled": bool(voice_calls_enabled),
        "preferred_language": preferred_language,
    }


# ── GET /calls/stats — summary statistics ─────────────────────────────────────
@router.get("/stats")
def get_call_stats(
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Return summary stats for call logs."""
    total = db["call_logs"].count_documents({})
    successful = db["call_logs"].count_documents({"call_status": "success"})
    failed = db["call_logs"].count_documents({"call_status": "failed"})
    return {
        "total_calls": total,
        "successful_calls": successful,
        "failed_calls": failed,
    }
