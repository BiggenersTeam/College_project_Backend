from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Any
from datetime import datetime


# --- Token Schemas ---
class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[str] = None
    role: Optional[str] = None


# --- User Schemas ---
class UserBase(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    role: str = "Police"  # Police / Admin
    profile_photo: Optional[str] = None


class UserCreate(UserBase):
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None
    profile_photo: Optional[str] = None


class UserOut(UserBase):
    id: str
    created_at: datetime
    profile_photo: Optional[str] = None
    provider: Optional[str] = "email"
    google_id: Optional[str] = None
    last_login: Optional[datetime] = None

    model_config = {"from_attributes": True}


# --- Missing Person Schemas ---
class MissingPersonBase(BaseModel):
    name: str
    age: int
    gender: str
    last_seen_location: str
    last_seen_date: datetime
    description: Optional[str] = None
    status: str = "Active"
    contact_phone: Optional[str] = None   # Phone number entered at registration


class MissingPersonCreate(MissingPersonBase):
    pass


class MissingPersonOut(MissingPersonBase):
    id: str
    photo_path: str
    created_at: datetime
    created_by: str
    latitude:   Optional[float] = None
    longitude:  Optional[float] = None
    contact_phone: Optional[str] = None

    # Resolution fields — populated when case is resolved
    resolved:            Optional[bool]     = None
    resolved_at:         Optional[datetime] = None
    matched_at:          Optional[datetime] = None
    matched_confidence:  Optional[float]    = None
    matched_image_url:   Optional[str]      = None
    matched_query_image: Optional[str]      = None
    resolved_by:         Optional[str]      = None
    resolution_method:   Optional[str]      = None
    last_updated:        Optional[datetime] = None

    model_config = {"from_attributes": True}


class MissingPersonUpdate(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    last_seen_location: Optional[str] = None
    last_seen_date: Optional[datetime] = None
    description: Optional[str] = None
    status: Optional[str] = None


# --- Search History Schemas ---
class SearchHistoryOut(BaseModel):
    id: str
    searched_photo_path: str
    search_date: datetime
    searched_by: str  # String ObjectId reference
    match_found: bool
    matched_person_id: Optional[str] = None
    confidence_score: Optional[float] = None
    matched_person: Optional[MissingPersonOut] = None
    matched_image_path: Optional[str] = None  # Persistent copy of matched person's photo

    model_config = {"from_attributes": True}


# --- Dashboard Stats Schema ---
class DashboardStats(BaseModel):
    total_missing_persons:     int
    total_investigation_cases: int   # Active cases
    tracing_cases:             int = 0
    resolved_cases:            int = 0
    closed_cases:              int = 0
    recent_searches_count:     int
    recent_searches:           List[SearchHistoryOut]
    cases_over_time:           List[dict]
    recent_activity:           List[dict] = []


# ── Vapi AI Phone Assistant Schemas ───────────────────────────────────────────

class VapiStartCallRequest(BaseModel):
    """Request body for POST /vapi/call"""
    phone_number: str = Field(
        ...,
        description="Recipient phone number. Will be validated to E.164 format.",
        examples=["+919876543210"],
    )
    case_id: Optional[str] = Field(
        default=None,
        description="Optional case ID to associate this call with a missing person case.",
    )
    first_message: Optional[str] = Field(
        default=None,
        description="Optional custom first message for the AI assistant to say.",
        max_length=500,
    )


class VapiStartCallResponse(BaseModel):
    """Response body for POST /vapi/call"""
    success: bool
    call_id: Optional[str] = None
    status: Optional[str] = None
    phone_number: Optional[str] = None
    message: str = ""
    error: Optional[str] = None


class VapiEndCallRequest(BaseModel):
    """Request body for POST /vapi/end-call"""
    call_id: str = Field(..., description="The Vapi call ID returned when the call was started.")


class VapiEndCallResponse(BaseModel):
    """Response body for POST /vapi/end-call"""
    success: bool
    call_id: Optional[str] = None
    status: Optional[str] = None
    message: str = ""
    error: Optional[str] = None


class VapiCallStatusResponse(BaseModel):
    """Response body for GET /vapi/call-status/{call_id}"""
    success: bool
    call_id: Optional[str] = None
    status: Optional[str] = None
    duration: Optional[int] = None          # Seconds
    ended_reason: Optional[str] = None
    error: Optional[str] = None


class VapiCallLogOut(BaseModel):
    """A single entry returned by GET /vapi/call-history"""
    id: str
    call_id: str
    phone_number: str
    user_id: str
    case_id: Optional[str] = None
    call_status: str
    start_time: Optional[str] = None        # ISO datetime string
    end_time: Optional[str] = None          # ISO datetime string
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}
