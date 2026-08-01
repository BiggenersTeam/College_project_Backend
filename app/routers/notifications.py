"""
FaceTracer Notifications + Search Suggestions Router
=====================================================
Endpoints:
  GET  /notifications              — list notifications for current user
  PATCH /notifications/read/{id}   — mark one as read
  PATCH /notifications/read-all    — mark all as read
  DELETE /notifications/{id}       — delete notification
  GET  /search-suggestions?q=...   — autocomplete suggestions from MongoDB
"""
import re
import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pymongo.database import Database
from ..database import get_db
from ..models import doc_to_dict, str_to_objectid
from ..auth import get_current_user

router = APIRouter(tags=["Notifications & Search"])


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH SUGGESTIONS — live autocomplete from MongoDB
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/search-suggestions")
def search_suggestions(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(8, ge=1, le=20),
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """
    Return autocomplete suggestions from MongoDB.
    Searches: name, last_seen_location (city/district/state).
    Returns up to `limit` results with type (person | location).
    """
    if not q or not q.strip():
        return []

    pattern = re.compile(re.escape(q.strip()), re.IGNORECASE)
    suggestions = []
    seen_values = set()

    # ── Person name matches ──────────────────────────────────────────────
    name_docs = list(
        db["missing_persons"].find(
            {"name": {"$regex": pattern}},
            {"name": 1, "status": 1}
        ).limit(limit)
    )
    for doc in name_docs:
        val = doc.get("name", "").strip()
        key = f"person:{val.lower()}"
        if val and key not in seen_values:
            seen_values.add(key)
            suggestions.append({
                "type":   "person",
                "value":  val,
                "status": doc.get("status", "Active"),
            })

    # ── Location matches ─────────────────────────────────────────────────
    loc_docs = list(
        db["missing_persons"].find(
            {"last_seen_location": {"$regex": pattern}},
            {"last_seen_location": 1}
        ).limit(limit)
    )
    for doc in loc_docs:
        val = doc.get("last_seen_location", "").strip()
        # Extract city-level part (before first comma)
        city = val.split(",")[0].strip() if "," in val else val
        key = f"location:{city.lower()}"
        if city and key not in seen_values:
            seen_values.add(key)
            suggestions.append({
                "type":  "location",
                "value": city,
            })

    return suggestions[:limit]


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/notifications")
def get_notifications(
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Return latest notifications for the current user."""
    docs = list(
        db["notifications"]
        .find({"userId": current_user["id"]})
        .sort("createdAt", -1)
        .limit(limit)
    )
    result = []
    for doc in docs:
        d = doc_to_dict(doc)
        if isinstance(d.get("createdAt"), datetime.datetime):
            d["createdAt"] = d["createdAt"].isoformat()
        result.append(d)
    return result


@router.get("/notifications/unread-count")
def get_unread_count(
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    count = db["notifications"].count_documents({
        "userId":  current_user["id"],
        "isRead":  False,
    })
    return {"unread": count}


@router.patch("/notifications/read-all")
def mark_all_read(
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    db["notifications"].update_many(
        {"userId": current_user["id"], "isRead": False},
        {"$set": {"isRead": True}},
    )
    return {"success": True}


@router.patch("/notifications/read/{notification_id}")
def mark_one_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    try:
        oid = str_to_objectid(notification_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid notification ID.")

    db["notifications"].update_one(
        {"_id": oid, "userId": current_user["id"]},
        {"$set": {"isRead": True}},
    )
    return {"success": True}


@router.delete("/notifications/{notification_id}", status_code=204)
def delete_notification(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    try:
        oid = str_to_objectid(notification_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid notification ID.")

    db["notifications"].delete_one({"_id": oid, "userId": current_user["id"]})


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION HELPER — called internally by other routers
# ══════════════════════════════════════════════════════════════════════════════

def create_notification(
    db,
    user_id:  str,
    title:    str,
    message:  str,
    notif_type: str,
    case_id:  Optional[str] = None,
):
    """
    Insert a notification document.
    notif_type: "case_registered" | "face_match" | "case_resolved" |
                "status_changed" | "ai_call" | "case_deleted" | "user_registered"
    """
    try:
        db["notifications"].insert_one({
            "userId":    user_id,
            "title":     title,
            "message":   message,
            "type":      notif_type,
            "caseId":    case_id,
            "isRead":    False,
            "createdAt": datetime.datetime.utcnow(),
        })
    except Exception:
        pass  # never crash the main flow
