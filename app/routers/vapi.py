"""
FaceTracer Vapi AI Phone Assistant Router
==========================================
REST API endpoints for the Vapi AI calling assistant.

All endpoints require JWT authentication — the Vapi private key is
NEVER exposed to the client; all Vapi communication happens server-side.

Routes:
  POST   /vapi/call                  — Start an outbound AI call
  POST   /vapi/end-call              — End an active call
  GET    /vapi/call-status/{call_id} — Get real-time status of a call
  GET    /vapi/call-history          — Get call history for current user
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pymongo.database import Database

from ..auth import get_current_user
from ..database import get_db
from ..schemas import (
    VapiStartCallRequest,
    VapiStartCallResponse,
    VapiEndCallRequest,
    VapiEndCallResponse,
    VapiCallStatusResponse,
    VapiCallLogOut,
)
from ..services import vapi_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/vapi",
    tags=["Vapi AI Phone Assistant"],
)


# ── POST /vapi/call ───────────────────────────────────────────────────────────
@router.post(
    "/call",
    response_model=VapiStartCallResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start an outbound AI phone call",
    description=(
        "Initiates an outbound AI-powered voice call to the given phone number "
        "using the Vapi AI assistant. Returns the Vapi call ID for tracking. "
        "The caller's phone number must be a valid E.164 number."
    ),
)
def start_call(
    body: VapiStartCallRequest,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """
    Start an AI phone call via Vapi.

    - Validates the phone number to E.164 format.
    - Authenticates against Vapi using the server-side private key.
    - Persists a call log to MongoDB (vapi_call_logs collection).
    - Returns the Vapi call ID so the client can poll for status.
    """
    user_id = current_user["id"]
    logger.info(
        f"[VapiRouter] start_call | User: {user_id} | "
        f"Phone: {body.phone_number} | Case: {body.case_id}"
    )

    result = vapi_service.start_call(
        phone_number=body.phone_number,
        user_id=user_id,
        case_id=body.case_id,
        first_message=body.first_message,
    )

    if not result["success"]:
        logger.warning(f"[VapiRouter] start_call failed | User: {user_id} | Error: {result['error']}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result["error"] or "Failed to initiate the call. Please try again.",
        )

    return VapiStartCallResponse(
        success=True,
        call_id=result["call_id"],
        status=result["status"],
        phone_number=result["phone_number"],
        message="Call successfully initiated. AI assistant is connecting...",
        error=None,
    )


# ── POST /vapi/end-call ───────────────────────────────────────────────────────
@router.post(
    "/end-call",
    response_model=VapiEndCallResponse,
    summary="End an active AI phone call",
    description="Terminates an active Vapi call by its call ID. Updates the call log in MongoDB.",
)
def end_call(
    body: VapiEndCallRequest,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """
    End an active Vapi call.

    - Sends a DELETE request to the Vapi API.
    - Treats 404 (already ended) as a successful operation.
    - Updates the call log end_time and status in MongoDB.
    """
    user_id = current_user["id"]
    logger.info(f"[VapiRouter] end_call | User: {user_id} | Call ID: {body.call_id}")

    result = vapi_service.end_call(
        call_id=body.call_id,
        user_id=user_id,
    )

    if not result["success"]:
        logger.warning(f"[VapiRouter] end_call failed | Call: {body.call_id} | Error: {result['error']}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result["error"] or "Failed to end the call.",
        )

    return VapiEndCallResponse(
        success=True,
        call_id=result["call_id"],
        status=result["status"],
        message="Call ended successfully.",
        error=None,
    )


# ── GET /vapi/call-status/{call_id} ──────────────────────────────────────────
@router.get(
    "/call-status/{call_id}",
    response_model=VapiCallStatusResponse,
    summary="Get real-time status of a Vapi call",
    description=(
        "Polls the Vapi API for the current status of a call. "
        "Also syncs the latest status back to MongoDB."
    ),
)
def get_call_status(
    call_id: str,
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """
    Get current status of a Vapi call.

    Possible statuses returned by Vapi: queued, ringing, in-progress,
    forwarding, ended, failed, busy, no-answer.
    """
    if not call_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="call_id path parameter cannot be empty.",
        )

    logger.info(f"[VapiRouter] get_call_status | Call: {call_id} | User: {current_user['id']}")

    result = vapi_service.get_call_status(call_id=call_id)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result["error"] or "Failed to retrieve call status.",
        )

    return VapiCallStatusResponse(
        success=True,
        call_id=result["call_id"],
        status=result["status"],
        duration=result.get("duration"),
        ended_reason=result.get("ended_reason"),
        error=None,
    )


# ── GET /vapi/call-history ────────────────────────────────────────────────────
@router.get(
    "/call-history",
    response_model=List[VapiCallLogOut],
    summary="Get call history for the authenticated user",
    description=(
        "Returns a list of past AI phone calls made by the current user, "
        "sorted by most recent first. Data is read from MongoDB."
    ),
)
def get_call_history(
    limit: int = Query(default=20, ge=1, le=100, description="Max records to return (1–100)."),
    current_user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """
    Retrieve call history from MongoDB for the authenticated user.

    Includes call ID, phone number, status, case association,
    and start/end timestamps.
    """
    user_id = current_user["id"]
    logger.info(f"[VapiRouter] get_call_history | User: {user_id} | Limit: {limit}")

    logs = vapi_service.get_call_history(user_id=user_id, limit=limit)
    return logs
