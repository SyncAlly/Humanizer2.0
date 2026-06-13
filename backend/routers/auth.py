"""
routers/auth.py

JWT validation for all protected routes.

How it works:
1. The frontend logs in via Supabase Google OAuth.
2. Supabase issues a JWT (access token) to the browser.
3. The browser sends that JWT in the Authorization header on every API call:
       Authorization: Bearer <token>
4. This module validates the JWT against Supabase's public keys.
5. If valid, it extracts the user's ID and email and passes them
   to the route handler via FastAPI dependency injection.
6. If invalid or missing, it returns 401 immediately.

This means no route handler ever needs to manually check auth —
they just declare `user: UserInfo = Depends(get_current_user)` and
receive a validated user object or never execute at all.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError, ExpiredSignatureError
import httpx

from core.config import get_settings
from models.schemas import UserInfo, SuccessResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

# FastAPI's built-in Bearer token extractor
# auto_error=False means we handle the missing token ourselves
# with a clearer error message
bearer_scheme = HTTPBearer(auto_error=False)


async def _get_supabase_jwks() -> dict:
    """
    Fetches Supabase's public JSON Web Key Set (JWKS).
    Used to verify the signature on JWTs without needing the secret key.
    Supabase publishes its public keys at a known URL.

    In production this should be cached with a TTL (e.g. 1 hour)
    to avoid a network call on every request. For this project,
    httpx's connection pooling makes it fast enough.
    """
    settings = get_settings()
    jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"

    async with httpx.AsyncClient() as client:
        response = await client.get(jwks_url, timeout=5.0)
        response.raise_for_status()
        return response.json()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserInfo:
    """
    FastAPI dependency — validates the Bearer JWT and returns user info.

    Usage in any protected route:
        @router.post("/api/humanize")
        async def humanize(user: UserInfo = Depends(get_current_user)):
            # user.user_id and user.email are guaranteed valid here

    Raises:
        HTTPException 401: Missing token, expired token, invalid signature,
                           or any other JWT validation failure.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing. Include: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    settings = get_settings()

    try:
        # Try HS256 first (newer Supabase projects sign with the JWT secret)
        try:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except Exception:
            # Fall back to asymmetric keys via JWKS.
            # Modern Supabase projects use ES256 (Elliptic Curve);
            # older projects may use RS256. We support both.
            jwks = await _get_supabase_jwks()
            keys = jwks.get("keys", [])
            if not keys:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not fetch auth public keys. Try again shortly.",
                )
            payload = jwt.decode(
                token,
                keys,
                algorithms=["RS256", "ES256"],
                audience="authenticated",
                options={"verify_exp": True},
            )

        user_id: str = payload.get("sub")
        email: str   = payload.get("email", "")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token payload missing user ID (sub claim).",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return UserInfo(user_id=user_id, email=email)

    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Auth validation error: {str(e)}",
        )

# ── Auth routes ────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserInfo)
async def get_me(user: UserInfo = Depends(get_current_user)):
    """
    Returns the current authenticated user's info.
    Useful for the frontend to confirm a session is still valid
    and display the logged-in user's email.

    GET /api/auth/me
    Headers: Authorization: Bearer <token>
    """
    return user


@router.get("/verify", response_model=SuccessResponse)
async def verify_token(user: UserInfo = Depends(get_current_user)):
    """
    Lightweight endpoint to verify a token is still valid.
    Frontend calls this on page load to check if the session is alive
    before showing the app UI.

    GET /api/auth/verify
    Headers: Authorization: Bearer <token>
    """
    return SuccessResponse(message=f"Token valid for user {user.user_id}")
