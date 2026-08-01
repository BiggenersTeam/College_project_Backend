import os
import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pymongo.database import Database
from ..database import get_db
from ..models import doc_to_dict
from ..schemas import UserCreate, UserLogin, Token, UserOut
from ..auth import hash_password, verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Register ─────────────────────────────────────────────────────────────────
@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(user_in: UserCreate, db: Database = Depends(get_db)):
    if db["users"].find_one({"email": user_in.email}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists."
        )
    new_user_doc = {
        "name": user_in.name,
        "email": user_in.email,
        "phone": user_in.phone,
        "password_hash": hash_password(user_in.password),
        "role": user_in.role,
        "provider": "email",
        "created_at": datetime.datetime.utcnow(),
        "last_login": datetime.datetime.utcnow(),
    }
    result = db["users"].insert_one(new_user_doc)
    return doc_to_dict(db["users"].find_one({"_id": result.inserted_id}))


# ── Email / Password Login ────────────────────────────────────────────────────
@router.post("/login", response_model=Token)
def login(credentials: UserLogin, db: Database = Depends(get_db)):
    user_doc = db["users"].find_one({"email": credentials.email})
    if not user_doc or not verify_password(credentials.password, user_doc.get("password_hash") or ""):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password."
        )
    # Update last login
    db["users"].update_one(
        {"email": credentials.email},
        {"$set": {"last_login": datetime.datetime.utcnow()}}
    )
    access_token = create_access_token(
        data={"sub": user_doc["email"], "role": user_doc.get("role", "Police")}
    )
    return {"access_token": access_token, "token_type": "bearer"}


# ── Google OAuth Login ────────────────────────────────────────────────────────
@router.post("/google", response_model=Token)
def google_login(payload: dict = Body(...), db: Database = Depends(get_db)):
    """
    Accepts two payload formats:

    Format 1 — Google ID token (from GSI renderButton / One Tap):
        { "id_token": "<google id token>" }

    Format 2 — Direct profile data (from OAuth2 popup code flow):
        { "googleId": "...", "name": "...", "email": "...", "picture": "..." }
    """
    GOOGLE_CLIENT_ID_ENV = os.getenv("GOOGLE_CLIENT_ID", "")

    google_email = None
    google_name = None
    google_id = None
    google_picture = None

    # ── Format 1: Verify Google ID token ──
    id_token_str = payload.get("id_token")
    if id_token_str:
        if not GOOGLE_CLIENT_ID_ENV:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Google OAuth is not configured on this server. Set GOOGLE_CLIENT_ID in backend .env"
            )
        try:
            from google.oauth2 import id_token as google_id_token_lib
            from google.auth.transport import requests as google_requests
            id_info = google_id_token_lib.verify_oauth2_token(
                id_token_str,
                google_requests.Request(),
                GOOGLE_CLIENT_ID_ENV
            )
            google_email   = id_info.get("email")
            google_name    = id_info.get("name", google_email)
            google_id      = id_info.get("sub")
            google_picture = id_info.get("picture")
        except ImportError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="google-auth library not installed. Run: pip install google-auth"
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid Google token: {str(e)}"
            )

    # ── Format 2: Direct profile payload ──
    else:
        google_id      = payload.get("googleId")
        google_name    = payload.get("name")
        google_email   = payload.get("email")
        google_picture = payload.get("picture")

        if not google_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google authentication payload is missing required fields (email)."
            )

    if not google_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account does not have a verified email."
        )

    now = datetime.datetime.utcnow()

    # ── Find existing user ──
    user_doc = db["users"].find_one({"email": google_email})

    if user_doc:
        # Update Google-specific fields and last login
        update_fields = {
            "last_login": now,
            "provider": user_doc.get("provider", "google"),
        }
        if google_id:
            update_fields["google_id"] = google_id
        if google_picture and not user_doc.get("profile_photo"):
            update_fields["profile_photo"] = google_picture
        if google_name and not user_doc.get("name"):
            update_fields["name"] = google_name

        db["users"].update_one({"email": google_email}, {"$set": update_fields})
        user_doc = db["users"].find_one({"email": google_email})
    else:
        # ── Create new Google user ──
        new_user_doc = {
            "name":          google_name or google_email,
            "email":         google_email,
            "phone":         None,
            "password_hash": None,
            "role":          "Police",
            "provider":      "google",
            "google_id":     google_id,
            "profile_photo": google_picture,
            "created_at":    now,
            "last_login":    now,
        }
        result = db["users"].insert_one(new_user_doc)
        user_doc = db["users"].find_one({"_id": result.inserted_id})

    # Issue JWT (same structure as email/password login)
    access_token = create_access_token(
        data={"sub": user_doc["email"], "role": user_doc.get("role", "Police")}
    )
    return {"access_token": access_token, "token_type": "bearer"}
