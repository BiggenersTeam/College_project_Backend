import os
import uuid
import base64
import logging
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pymongo.database import Database
from ..database import get_db
from ..models import doc_to_dict, str_to_objectid
from ..schemas import UserOut, UserUpdate
from ..auth import get_current_user, hash_password

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/users",
    tags=["Users"]
)

# Local upload dir — fallback only
UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "uploads", "profiles"
)
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _save_photo_as_base64(photo_bytes: bytes, filename: str) -> str:
    """
    Convert image bytes to Base64 data URI.
    Stored in MongoDB — permanent across all restarts, no external service.
    """
    ext  = (os.path.splitext(filename)[1] or ".jpg").lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp",
            ".gif": "image/gif"}.get(ext, "image/jpeg")
    b64  = base64.b64encode(photo_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _save_photo_locally(photo_bytes: bytes, filename: str) -> str:
    """Fallback: save to local filesystem (dev only)."""
    save_path = os.path.join(UPLOAD_DIR, filename)
    with open(save_path, "wb") as f:
        f.write(photo_bytes)
    return f"uploads/profiles/{filename}"


@router.get("/me", response_model=UserOut)
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user


@router.put("/me", response_model=UserOut)
def update_profile(
    profile_data: UserUpdate,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    update_fields = {}
    if profile_data.name is not None:
        update_fields["name"] = profile_data.name
    if profile_data.phone is not None:
        update_fields["phone"] = profile_data.phone
    if profile_data.role is not None:
        update_fields["role"] = profile_data.role
    if profile_data.password is not None and profile_data.password != "":
        update_fields["password_hash"] = hash_password(profile_data.password)
    if profile_data.profile_photo is not None:
        update_fields["profile_photo"] = profile_data.profile_photo

    if update_fields:
        db["users"].update_one(
            {"_id": str_to_objectid(current_user["id"])},
            {"$set": update_fields}
        )
    updated_user = db["users"].find_one({"_id": str_to_objectid(current_user["id"])})
    return doc_to_dict(updated_user)


@router.post("/me/photo", response_model=UserOut)
def upload_profile_photo(
    photo: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Upload profile photo.
    Strategy: Base64 in MongoDB (primary) → local filesystem (dev fallback).
    Base64 is permanent — survives Render restarts and redeployments.
    """
    photo_bytes = photo.file.read()
    if len(photo_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Photo size must be under 5MB.")

    profile_photo_value = None

    # Strategy 1: Base64 in MongoDB — works everywhere, permanent
    try:
        profile_photo_value = _save_photo_as_base64(
            photo_bytes, photo.filename or "profile.jpg")
        logger.info(f"Profile photo stored as base64 for user {current_user['id']}")
    except Exception as e:
        logger.warning(f"Base64 conversion failed: {e}")

    # Strategy 2: Local filesystem fallback (dev / VPS only)
    if not profile_photo_value:
        try:
            ext      = (os.path.splitext(photo.filename)[1] or ".jpg").lower()
            filename = f"profile_{current_user['id']}_{uuid.uuid4().hex}{ext}"
            profile_photo_value = _save_photo_locally(photo_bytes, filename)
            logger.info(f"Profile photo saved locally: {profile_photo_value}")
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save profile photo: {str(e)}"
            )

    db["users"].update_one(
        {"_id": str_to_objectid(current_user["id"])},
        {"$set": {"profile_photo": profile_photo_value}}
    )
    updated_user = db["users"].find_one({"_id": str_to_objectid(current_user["id"])})
    return doc_to_dict(updated_user)
