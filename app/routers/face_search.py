import os
import uuid
import json
import base64
import shutil
import logging
import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, BackgroundTasks
from pymongo.database import Database
from ..database import get_db
from ..models import doc_to_dict, str_to_objectid
from ..schemas import SearchHistoryOut
from ..auth import get_current_user
from ..services.face_recognition_service import face_recognition_service
from ..services.events import dispatch_case_resolved
from ..routers.notifications import create_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/face-search", tags=["Face Search"])

UPLOAD_DIR   = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
SEARCHES_DIR = os.path.join(UPLOAD_DIR, "searches")
MATCHED_DIR  = os.path.join(UPLOAD_DIR, "matched")
os.makedirs(SEARCHES_DIR, exist_ok=True)
os.makedirs(MATCHED_DIR,  exist_ok=True)


def _to_base64(photo_bytes: bytes, filename: str = "photo.jpg") -> str:
    """Convert image bytes to Base64 data URI for permanent MongoDB storage."""
    ext  = (os.path.splitext(filename)[1] or ".jpg").lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp",
            ".gif": "image/gif"}.get(ext, "image/jpeg")
    b64  = base64.b64encode(photo_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _get_matched_photo(person: dict) -> Optional[str]:
    """
    Return the registered person's photo_path for use as matched_image_path.
    - If already Base64 or HTTPS → return as-is.
    - If local relative path → try to read and convert to Base64.
    - Fallback → return original path string.
    """
    path = person.get("photo_path", "")
    if not path:
        return None

    # Already Base64 or absolute URL — use directly
    if path.startswith("data:") or path.startswith("http://") or path.startswith("https://"):
        return path

    # Legacy local relative path — try to resolve and convert
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        clean    = path.lstrip("/")
        candidates = [
            os.path.join(base_dir, "app", clean),
            os.path.join(base_dir, clean),
            os.path.join(base_dir, "app", "uploads", os.path.basename(clean)),
        ]
        local_abs = next((p for p in candidates if os.path.exists(p)), None)
        if local_abs:
            with open(local_abs, "rb") as f:
                return _to_base64(f.read(), os.path.basename(local_abs))
    except Exception as e:
        logger.warning(f"Could not convert matched photo to base64: {e}")

    return path  # return original path as last resort


# ── Helper: embed matched_person into search history doc ─────────────────────
def _build_search_history_out(search_doc: dict, db: Database) -> dict:
    result = doc_to_dict(search_doc)
    matched_person_id = result.get("matched_person_id")
    if matched_person_id:
        try:
            person_doc = db["missing_persons"].find_one(
                {"_id": str_to_objectid(matched_person_id)})
            result["matched_person"] = doc_to_dict(person_doc) if person_doc else None
        except Exception:
            result["matched_person"] = None
    else:
        result["matched_person"] = None
    return result


# ── Helper: resolve case in MongoDB and create audit/match records ────────────
def _resolve_case(
    db: Database,
    person: dict,
    confidence: float,
    query_image_path: str,
    matched_image_path: str,
    resolved_by_user_id: str,
    background_tasks: BackgroundTasks,
):
    person_id  = str(person["_id"])
    old_status = person.get("status", "Active")
    now        = datetime.datetime.utcnow()

    resolved_statuses = {"resolved", "solved", "found", "traced", "closed"}
    if old_status.lower() in resolved_statuses:
        logger.info(f"Case {person_id} already resolved. Skipping.")
        return

    db["missing_persons"].update_one(
        {"_id": person["_id"]},
        {"$set": {
            "status":             "Resolved",
            "resolved":           True,
            "resolved_at":        now,
            "matched_at":         now,
            "matched_confidence": round(confidence, 2),
            "matched_image_url":  matched_image_path,
            "matched_query_image": query_image_path,
            "resolved_by":        "FaceTracer AI",
            "resolution_method":  "Face Recognition",
            "last_updated":       now,
        }}
    )
    logger.info(f"Case {person_id} resolved. Confidence: {confidence:.1f}%")

    db["match_histories"].insert_one({
        "case_id":           person_id,
        "matched_person_id": person_id,
        "confidence_score":  round(confidence, 2),
        "query_image":       query_image_path,
        "registered_image":  person.get("photo_path"),
        "matched_image":     matched_image_path,
        "matched_at":        now,
        "resolved_by":       "FaceTracer AI",
        "resolution_method": "Face Recognition",
        "resolved_by_user":  resolved_by_user_id,
    })

    db["audit_logs"].insert_one({
        "case_id":       person_id,
        "old_status":    old_status,
        "new_status":    "Resolved",
        "changed_at":    now,
        "changed_by":    "FaceTracer AI",
        "user_id":       resolved_by_user_id,
        "ai_confidence": round(confidence, 2),
        "action":        "CASE_RESOLVED_BY_FACE_RECOGNITION",
    })

    db_uri       = os.getenv("MONGODB_URI", "")
    fresh_person = db["missing_persons"].find_one({"_id": person["_id"]}) or person
    background_tasks.add_task(
        dispatch_case_resolved,
        case=fresh_person,
        new_status="Resolved",
        db_uri=db_uri,
        resolved_by_user_id=resolved_by_user_id,
        confidence=confidence,
    )
    logger.info(f"Resolution event dispatched for case {person_id}")


# ── Main face search endpoint ─────────────────────────────────────────────────
@router.post("", response_model=SearchHistoryOut)
def face_search(
    background_tasks: BackgroundTasks,
    photo: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    # Read photo bytes
    photo_bytes = photo.file.read()

    # Save temp file for OpenCV face encoding
    ext             = os.path.splitext(photo.filename)[1] or ".jpg"
    tmp_filename    = f"{uuid.uuid4().hex}{ext}"
    saved_path      = os.path.join(SEARCHES_DIR, tmp_filename)

    try:
        with open(saved_path, "wb") as f:
            f.write(photo_bytes)
        logger.info(f"Search photo temp saved: {saved_path}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to save query image: {str(e)}")

    # Extract face encoding
    query_encoding = face_recognition_service.get_face_encoding(saved_path)

    # Convert search photo to Base64 for permanent storage
    search_photo_b64 = _to_base64(photo_bytes, photo.filename or tmp_filename)

    # Clean up temp file
    try:
        os.remove(saved_path)
    except Exception:
        pass

    if query_encoding is None:
        no_face_doc = {
            "searched_photo_path": search_photo_b64,
            "search_date":         datetime.datetime.utcnow(),
            "searched_by":         current_user["id"],
            "match_found":         False,
            "matched_person_id":   None,
            "confidence_score":    0.0,
            "matched_image_path":  None,
            "no_face_detected":    True,
        }
        result = db["search_histories"].insert_one(no_face_doc)
        saved  = db["search_histories"].find_one({"_id": result.inserted_id})
        out    = _build_search_history_out(saved, db)
        out["error_hint"] = (
            "No face was detected in the uploaded image. "
            "Please upload a clear, close-up frontal portrait photo."
        )
        return out

    # Fetch Active/Tracing persons with face encodings
    missing_persons = list(db["missing_persons"].find(
        {"face_encoding": {"$ne": None}, "status": {"$in": ["Active", "Tracing"]}}
    ))

    best_person     = None
    best_similarity = -2.0
    best_confidence = 0.0

    for person in missing_persons:
        try:
            db_encoding = json.loads(person["face_encoding"])
            if db_encoding is None:
                continue
            similarity, confidence = face_recognition_service.match_faces(
                query_encoding, db_encoding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_confidence = confidence
                best_person     = person
        except Exception as e:
            logger.warning(f"Error matching person {person.get('_id')}: {e}")
            continue

    MATCH_THRESHOLD    = float(os.getenv("FACE_MATCH_THRESHOLD", "70.0"))
    is_match           = False
    matched_id         = None
    final_confidence   = best_confidence
    matched_image_path = None

    logger.info(
        f"Face search | Best: {best_confidence:.1f}% | Threshold: {MATCH_THRESHOLD}% | "
        f"Person: {str(best_person.get('_id','none')) if best_person else 'none'}"
    )

    if best_person is not None and best_confidence >= MATCH_THRESHOLD:
        is_match   = True
        matched_id = str(best_person["_id"])

        # Get matched person's photo (Base64 or convert legacy local path)
        matched_image_path = _get_matched_photo(best_person)

        _resolve_case(
            db=db,
            person=best_person,
            confidence=best_confidence,
            query_image_path=search_photo_b64,
            matched_image_path=matched_image_path,
            resolved_by_user_id=current_user["id"],
            background_tasks=background_tasks,
        )

        create_notification(
            db,
            user_id=current_user["id"],
            title="✅ Face Match Found",
            message=f"{best_person.get('name')}'s face matched with {best_confidence:.1f}% confidence.",
            notif_type="face_match",
            case_id=matched_id,
        )

    new_search_doc = {
        "searched_photo_path": search_photo_b64,
        "search_date":         datetime.datetime.utcnow(),
        "searched_by":         current_user["id"],
        "match_found":         is_match,
        "matched_person_id":   matched_id,
        "confidence_score":    final_confidence,
        "matched_image_path":  matched_image_path,
    }

    result = db["search_histories"].insert_one(new_search_doc)
    saved  = db["search_histories"].find_one({"_id": result.inserted_id})
    return _build_search_history_out(saved, db)


# ── Direct resolve endpoint ───────────────────────────────────────────────────
@router.post("/resolve/{case_id}")
def resolve_case_now(
    case_id:      str,
    confidence:   float = 70.0,
    query_image:  str   = "",
    current_user: dict  = Depends(get_current_user),
    db:           Database = Depends(get_db)
):
    resolved_statuses = {"resolved", "solved", "found", "traced", "closed"}
    try:
        oid = str_to_objectid(case_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid case ID")

    person = db["missing_persons"].find_one({"_id": oid})
    if not person:
        raise HTTPException(status_code=404, detail="Case not found")

    current_status = (person.get("status", "Active") or "Active")
    if current_status.lower() in resolved_statuses:
        updated = db["missing_persons"].find_one({"_id": oid})
        return {"success": True, "message": "Already resolved",
                "status": updated.get("status")}

    now = datetime.datetime.utcnow()
    db["missing_persons"].update_one(
        {"_id": oid},
        {"$set": {
            "status":             "Resolved",
            "resolved":           True,
            "resolved_at":        now,
            "matched_at":         now,
            "matched_confidence": round(confidence, 2),
            "matched_query_image": query_image,
            "resolved_by":        "FaceTracer AI",
            "resolution_method":  "AI Face Recognition",
            "last_updated":       now,
        }}
    )

    existing = db["audit_logs"].find_one({"case_id": case_id, "new_status": "Resolved"})
    if not existing:
        db["audit_logs"].insert_one({
            "case_id":       case_id,
            "old_status":    current_status,
            "new_status":    "Resolved",
            "changed_at":    now,
            "changed_by":    "FaceTracer AI",
            "user_id":       current_user["id"],
            "ai_confidence": round(confidence, 2),
            "action":        "CASE_RESOLVED_DIRECT",
        })

    existing_match = db["match_histories"].find_one({"case_id": case_id})
    if not existing_match:
        db["match_histories"].insert_one({
            "case_id":           case_id,
            "matched_person_id": case_id,
            "confidence_score":  round(confidence, 2),
            "query_image":       query_image,
            "matched_at":        now,
            "resolved_by":       "FaceTracer AI",
        })

    updated_person = db["missing_persons"].find_one({"_id": oid}) or person
    db_uri         = os.getenv("MONGODB_URI", "")

    import threading
    threading.Thread(
        target=dispatch_case_resolved,
        kwargs={
            "case":                updated_person,
            "new_status":          "Resolved",
            "db_uri":              db_uri,
            "resolved_by_user_id": current_user["id"],
            "confidence":          confidence,
        },
        daemon=True
    ).start()

    logger.info(f"Case {case_id} resolved directly by user {current_user['id']}")
    return {
        "success":     True,
        "message":     "Case resolved successfully",
        "status":      updated_person.get("status"),
        "case_id":     case_id,
        "confidence":  confidence,
        "person_name": updated_person.get("name"),
    }


# ── Debug endpoint ────────────────────────────────────────────────────────────
@router.post("/debug", include_in_schema=True)
def face_search_debug(
    photo: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    ext             = os.path.splitext(photo.filename)[1] or ".jpg"
    unique_filename = f"debug_{uuid.uuid4().hex}{ext}"
    saved_path      = os.path.join(SEARCHES_DIR, unique_filename)

    with open(saved_path, "wb") as f:
        f.write(photo.file.read())

    query_encoding = face_recognition_service.get_face_encoding(saved_path)
    try:
        os.remove(saved_path)
    except Exception:
        pass

    if query_encoding is None:
        return {"face_detected": False, "dnn_active": face_recognition_service.dnn_active,
                "message": "No face detected.", "matches": []}

    threshold   = float(os.getenv("FACE_MATCH_THRESHOLD", "70.0"))
    all_persons = list(db["missing_persons"].find({"face_encoding": {"$ne": None}}))
    scores      = []

    for person in all_persons:
        try:
            db_encoding = json.loads(person["face_encoding"])
            if db_encoding is None:
                continue
            similarity, confidence = face_recognition_service.match_faces(
                query_encoding, db_encoding)
            scores.append({
                "case_id":    str(person["_id"]),
                "name":       person.get("name", "Unknown"),
                "status":     person.get("status", "Active"),
                "similarity": round(similarity, 4),
                "confidence": round(confidence, 2),
                "would_match": confidence >= threshold,
            })
        except Exception as e:
            scores.append({"case_id": str(person.get("_id", "")),
                           "name": person.get("name", "Unknown"), "error": str(e)})

    scores.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return {
        "face_detected": True,
        "dnn_active":    face_recognition_service.dnn_active,
        "threshold":     threshold,
        "encoding_dims": len(query_encoding),
        "total_cases":   len(all_persons),
        "matches":       scores,
    }
