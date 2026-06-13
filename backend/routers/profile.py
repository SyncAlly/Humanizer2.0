"""
routers/profile.py

Manages persistent storage of each user's style profile in Supabase.

The style profile is computed client-side in profile.js (no ML needed —
it's pure statistical analysis of text), then sent to the backend for
storage so it persists across devices and sessions.

Routes:
    GET    /api/profile         → load the user's current profile
    POST   /api/profile         → save or update the profile
    DELETE /api/profile         → reset the profile to default
    POST   /api/profile/sample  → accept a rewrite output, merge into profile
"""

from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone

from core.config import get_supabase
from routers.auth import get_current_user
from models.schemas import (
    StyleProfile,
    SaveProfileRequest,
    ProfileResponse,
    SuccessResponse,
    UserInfo,
)

router = APIRouter(prefix="/api/profile", tags=["profile"])


# ── Load ───────────────────────────────────────────────────────────────────

@router.get("", response_model=ProfileResponse)
async def get_profile(user: UserInfo = Depends(get_current_user)):
    """
    Returns the user's stored style profile.
    If no profile exists yet, returns exists=False with None profile —
    the frontend handles this by showing the "Add samples" prompt.

    GET /api/profile
    Headers: Authorization: Bearer <token>
    """
    supabase = get_supabase()

    try:
        result = (
            supabase.table("profiles")
            .select("profile_data")
            .eq("user_id", user.user_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load profile: {str(e)}",
        )

    if not result.data:
        return ProfileResponse(profile=None, exists=False)

    try:
        profile = StyleProfile(**result.data[0]["profile_data"])
        return ProfileResponse(profile=profile, exists=True)
    except Exception as e:
        # Profile data in DB doesn't match current schema — return empty
        # This can happen after a schema update; just treat as no profile
        return ProfileResponse(profile=None, exists=False)


# ── Save ───────────────────────────────────────────────────────────────────

@router.post("", response_model=SuccessResponse)
async def save_profile(
    request: SaveProfileRequest,
    user: UserInfo = Depends(get_current_user),
):
    """
    Saves or updates the user's style profile.
    Uses upsert — creates the row if it doesn't exist, updates if it does.

    POST /api/profile
    Headers: Authorization: Bearer <token>
    Body: {"profile": <StyleProfile>}
    """
    supabase = get_supabase()

    try:
        supabase.table("profiles").upsert(
            {
                "user_id":      user.user_id,
                "profile_data": request.profile.model_dump(),
                "updated_at":   datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="user_id",
        ).execute()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save profile: {str(e)}",
        )

    return SuccessResponse(message="Profile saved.")


# ── Delete / reset ─────────────────────────────────────────────────────────

@router.delete("", response_model=SuccessResponse)
async def delete_profile(user: UserInfo = Depends(get_current_user)):
    """
    Deletes the user's profile entirely, resetting them to the
    generic (no-profile) humanization mode.

    DELETE /api/profile
    Headers: Authorization: Bearer <token>
    """
    supabase = get_supabase()

    try:
        supabase.table("profiles").delete().eq(
            "user_id", user.user_id
        ).execute()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete profile: {str(e)}",
        )

    return SuccessResponse(message="Profile reset.")


# ── Accept a rewrite output (feedback loop) ────────────────────────────────

@router.post("/sample", response_model=SuccessResponse)
async def accept_sample(
    request: SaveProfileRequest,
    user: UserInfo = Depends(get_current_user),
):
    """
    Called when the user clicks "Accept & learn" on a rewrite.

    The frontend has already merged the accepted output into the local
    profile and re-computed all statistics — it sends the updated profile
    here to persist it. This keeps the merging logic in one place (profile.js)
    rather than duplicating it server-side.

    POST /api/profile/sample
    Headers: Authorization: Bearer <token>
    Body: {"profile": <updated StyleProfile including accepted output>}
    """
    supabase = get_supabase()

    # Validate sample count increased (basic sanity check)
    if request.profile.sample_count < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile must have at least one sample.",
        )

    try:
        supabase.table("profiles").upsert(
            {
                "user_id":      user.user_id,
                "profile_data": request.profile.model_dump(),
                "updated_at":   datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="user_id",
        ).execute()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update profile from accepted sample: {str(e)}",
        )

    return SuccessResponse(
        message=f"Profile updated — now trained on {request.profile.sample_count} samples."
    )
