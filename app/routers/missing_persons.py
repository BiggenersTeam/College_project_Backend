import os
import uuid
import json
import base64
import datetime
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, BackgroundTasks
from pymongo.database import Database
from ..database import get_db
from ..models import doc_to_dict, str_to_objectid
from ..schemas import MissingPersonOut
from ..auth import get_current_user
from ..services.face_recognition_service import face_recognition_service
from ..services.events import dispatch_case_resolved
from ..services.geocoding_service import geocode_location
from ..routers.notifications import create_notification

router = APIRouter(
    prefix="/missing-persons",
    tags=["Missing Persons"]
)

# Local upload dir — used only as temp storage for face encoding extraction
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

RESOLVED_STATUSES = {"resolved", "solved", "found", "traced"}


def _to_base64(photo_bytes: bytes, filename: str) -> str:
    """
    Convert image bytes to a Base64 data URI.
    Stored directly in MongoDB — permanent across all restarts and deployments.
    No external service, no credentials required.
    """
    ext  = (os.path.splitext(filename)[1] or ".jpg").lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp",
            ".gif": "image/gif"}.get(ext, "image/jpeg")
    b64  = base64.b64encode(photo_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


@router.post("", response_model=MissingPersonOut, status_code=status.HTTP_201_CREATED)
def register_missing_person(
    name: str = Form(...),
    age: int = Form(...),
    gender: str = Form(...),
    last_seen_location: str = Form(...),
    last_seen_date: str = Form(...),
    description: Optional[str] = Form(None),
    status_str: str = Form("Active", alias="status"),
    contact_phone: Optional[str] = Form(None),
    photo: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    # Read photo bytes once
    photo_bytes = photo.file.read()
    if not photo_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Uploaded photo is empty.")

    # Save to temp file so OpenCV can extract face encoding
    ext             = os.path.splitext(photo.filename)[1] or ".jpg"
    tmp_filename    = f"{uuid.uuid4().hex}{ext}"
    tmp_path        = os.path.join(UPLOAD_DIR, tmp_filename)
    try:
        with open(tmp_path, "wb") as f:
            f.write(photo_bytes)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to process image: {str(e)}")

    # Extract face encoding (needs local file)
    face_encoding       = face_recognition_service.get_face_encoding(tmp_path)
    serialized_encoding = json.dumps(face_encoding) if face_encoding is not None else None

    # Delete temp file — no longer needed
    try:
        os.remove(tmp_path)
    except Exception:
        pass

    # Convert to Base64 data URI — this is what gets stored in MongoDB permanently
    photo_data_uri = _to_base64(photo_bytes, photo.filename or tmp_filename)

    # Parse date
    try:
        parsed_date = datetime.datetime.fromisoformat(last_seen_date.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_date = datetime.datetime.strptime(last_seen_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Invalid date format. Use ISO format or YYYY-MM-DD.")

    # Geocode
    coords    = geocode_location(last_seen_location)
    latitude  = coords[0] if coords else None
    longitude = coords[1] if coords else None

    new_person_doc = {
        "name":               name,
        "age":                age,
        "gender":             gender,
        "last_seen_location": last_seen_location,
        "last_seen_date":     parsed_date,
        "description":        description,
        "photo_path":         photo_data_uri,   # Base64 data URI — permanent
        "face_encoding":      serialized_encoding,
        "status":             status_str,
        "latitude":           latitude,
        "longitude":          longitude,
        "contact_phone":      contact_phone.strip() if contact_phone else None,
        "created_at":         datetime.datetime.utcnow(),
        "created_by":         current_user["id"],
    }

    result  = db["missing_persons"].insert_one(new_person_doc)
    created = db["missing_persons"].find_one({"_id": result.inserted_id})

    create_notification(
        db,
        user_id=current_user["id"],
        title="📁 New Case Registered",
        message=f"Missing person case for {name} has been registered successfully.",
        notif_type="case_registered",
        case_id=str(result.inserted_id),
    )
    return doc_to_dict(created)


@router.get("", response_model=list[MissingPersonOut])
def list_missing_persons(
    search: Optional[str] = None,
    gender: Optional[str] = None,
    status: Optional[str] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    query_filter = {}

    if search:
        pattern = re.compile(re.escape(search), re.IGNORECASE)
        query_filter["$or"] = [
            {"name": {"$regex": pattern}},
            {"last_seen_location": {"$regex": pattern}}
        ]
    if gender:
        query_filter["gender"] = gender
    if status:
        query_filter["status"] = status

    if age_min is not None or age_max is not None:
        age_filter = {}
        if age_min is not None:
            age_filter["$gte"] = age_min
        if age_max is not None:
            age_filter["$lte"] = age_max
        query_filter["age"] = age_filter

    if date_from or date_to:
        date_filter = {}
        if date_from:
            try:
                date_filter["$gte"] = datetime.datetime.fromisoformat(date_from.replace("Z", "+00:00"))
            except ValueError:
                pass
        if date_to:
            try:
                date_filter["$lte"] = datetime.datetime.fromisoformat(date_to.replace("Z", "+00:00"))
            except ValueError:
                pass
        if date_filter:
            query_filter["last_seen_date"] = date_filter

    persons = list(db["missing_persons"].find(query_filter).sort("created_at", -1))
    result  = []
    for p in persons:
        d = doc_to_dict(p)
        if not d.get("status"):
            d["status"] = "Active"
        if d.get("latitude") is None and d.get("last_seen_location"):
            coords = geocode_location(d["last_seen_location"])
            if coords:
                d["latitude"]  = coords[0]
                d["longitude"] = coords[1]
                try:
                    db["missing_persons"].update_one(
                        {"_id": str_to_objectid(d["id"])},
                        {"$set": {"latitude": coords[0], "longitude": coords[1]}}
                    )
                except Exception:
                    pass
        result.append(d)
    return result


@router.get("/{id}", response_model=MissingPersonOut)
def get_missing_person(
    id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    try:
        oid = str_to_objectid(id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format.")

    person = db["missing_persons"].find_one({"_id": oid})
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Missing person record not found.")
    return doc_to_dict(person)


@router.put("/{id}", response_model=MissingPersonOut)
def update_missing_person(
    id: str,
    background_tasks: BackgroundTasks,
    name: Optional[str] = Form(None),
    age: Optional[int] = Form(None),
    gender: Optional[str] = Form(None),
    last_seen_location: Optional[str] = Form(None),
    last_seen_date: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    try:
        oid = str_to_objectid(id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format.")

    person = db["missing_persons"].find_one({"_id": oid})
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Missing person record not found.")

    old_status    = person.get("status", "Active")
    update_fields = {}

    if name is not None:
        update_fields["name"] = name
    if age is not None:
        update_fields["age"] = age
    if gender is not None:
        update_fields["gender"] = gender
    if last_seen_location is not None:
        update_fields["last_seen_location"] = last_seen_location
        coords = geocode_location(last_seen_location)
        update_fields["latitude"]  = coords[0] if coords else None
        update_fields["longitude"] = coords[1] if coords else None
    if description is not None:
        update_fields["description"] = description
    if status is not None:
        update_fields["status"] = status
    if last_seen_date is not None:
        try:
            update_fields["last_seen_date"] = datetime.datetime.fromisoformat(
                last_seen_date.replace("Z", "+00:00"))
        except ValueError:
            pass

    if photo is not None:
        photo_bytes = photo.file.read()
        ext         = os.path.splitext(photo.filename)[1] or ".jpg"
        tmp_name    = f"{uuid.uuid4().hex}{ext}"
        tmp_path    = os.path.join(UPLOAD_DIR, tmp_name)

        # Temp save for face encoding
        with open(tmp_path, "wb") as f:
            f.write(photo_bytes)

        face_encoding = face_recognition_service.get_face_encoding(tmp_path)
        update_fields["face_encoding"] = json.dumps(face_encoding) if face_encoding is not None else None

        try:
            os.remove(tmp_path)
        except Exception:
            pass

        # Store as Base64 in MongoDB — permanent
        update_fields["photo_path"] = _to_base64(photo_bytes, photo.filename or tmp_name)

    if update_fields:
        db["missing_persons"].update_one({"_id": oid}, {"$set": update_fields})

    new_status = update_fields.get("status", old_status)
    if new_status != old_status:
        icons = {"Resolved": "✅", "Tracing": "🔍", "Closed": "🔒", "Active": "🟡"}
        icon  = icons.get(new_status, "📋")
        create_notification(
            db,
            user_id=current_user["id"],
            title=f"{icon} Case Status Changed",
            message=f"{person.get('name')} case status changed from {old_status} to {new_status}.",
            notif_type="status_changed",
            case_id=str(oid),
        )

    new_status = update_fields.get("status", old_status)
    if (new_status.lower() in RESOLVED_STATUSES
            and old_status.lower() not in RESOLVED_STATUSES):
        db_uri       = os.getenv("MONGODB_URI", "")
        fresh_person = db["missing_persons"].find_one({"_id": oid}) or person
        background_tasks.add_task(
            dispatch_case_resolved,
            case=fresh_person,
            new_status=new_status,
            db_uri=db_uri,
            resolved_by_user_id=current_user["id"],
        )

    updated = db["missing_persons"].find_one({"_id": oid})
    return doc_to_dict(updated)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_missing_person(
    id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    try:
        oid = str_to_objectid(id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format.")

    result = db["missing_persons"].delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Missing person record not found.")


@router.patch("/{id}/status", response_model=MissingPersonOut)
def update_case_status(
    id:               str,
    background_tasks: BackgroundTasks,
    new_status:       str = Form(...),
    current_user:     dict = Depends(get_current_user),
    db:               Database = Depends(get_db),
):
    try:
        oid = str_to_objectid(id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format.")

    person = db["missing_persons"].find_one({"_id": oid})
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Missing person record not found.")

    old_status = (person.get("status") or "Active")

    allowed = {"Active", "Tracing", "Resolved", "Closed", "Solved", "Found"}
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status '{new_status}'. Allowed: {', '.join(sorted(allowed))}"
        )

    now           = datetime.datetime.utcnow()
    update_fields = {"status": new_status, "last_updated": now}

    if new_status.lower() in RESOLVED_STATUSES and old_status.lower() not in RESOLVED_STATUSES:
        update_fields["resolved"]    = True
        update_fields["resolved_at"] = now
        update_fields["resolved_by"] = current_user.get("name", "Investigator")

    db["missing_persons"].update_one({"_id": oid}, {"$set": update_fields})

    try:
        db["audit_logs"].insert_one({
            "case_id":    str(oid),
            "old_status": old_status,
            "new_status": new_status,
            "changed_at": now,
            "changed_by": current_user.get("name", "Investigator"),
            "user_id":    current_user["id"],
            "action":     "STATUS_CHANGE_PATCH",
        })
    except Exception:
        pass

    if new_status.lower() in RESOLVED_STATUSES and old_status.lower() not in RESOLVED_STATUSES:
        db_uri = os.getenv("MONGODB_URI", "")
        fresh  = db["missing_persons"].find_one({"_id": oid}) or person
        background_tasks.add_task(
            dispatch_case_resolved,
            case=fresh,
            new_status=new_status,
            db_uri=db_uri,
            resolved_by_user_id=current_user["id"],
        )

    updated = db["missing_persons"].find_one({"_id": oid})
    return doc_to_dict(updated)
