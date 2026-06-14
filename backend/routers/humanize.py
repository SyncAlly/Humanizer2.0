"""
routers/humanize.py

The core route of the application: POST /api/humanize

Request flow:
1. Validate JWT → get user ID              (auth dependency)
2. Check rate limit → increment counter    (rate_limiter)
3. Fetch encrypted API key from Supabase   (DB query)
4. Decrypt API key                         (encryption)
5. Build system prompt from style profile  (prompt_builder)
6. Stream Gemini API response              (httpx streaming)
7. Forward stream to frontend              (StreamingResponse)

Also exposes:
- GET  /api/humanize/usage   → current rate limit status for the user
- POST /api/key              → save/update encrypted Gemini API key
- GET  /api/key/status       → check whether a key is stored (no exposure)
"""

import json
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from core.config import get_settings, get_supabase
from core.db import run_db
from core.encryption import encrypt_api_key, decrypt_api_key
from core.rate_limiter import check_and_increment, get_usage
from core.prompt_builder import build_system_prompt, build_user_message
from routers.auth import get_current_user
from models.schemas import (
    HumanizeRequest,
    HumanizeResponse,
    HumanizeStrength,
    KeyStatusResponse,
    SaveKeyRequest,
    SuccessResponse,
    UserInfo,
)

router = APIRouter(prefix="/api", tags=["humanize"])

# Gemini API config
GEMINI_MODEL    = "gemini-3.5-flash"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_TIMEOUT  = 60.0  # seconds — streaming can take a while for long texts


# ── Helper: fetch and decrypt the user's stored Gemini key ────────────────

async def _get_user_gemini_key(user_id: str) -> str:
    """
    Fetches the encrypted Gemini API key for a user from Supabase
    and decrypts it server-side.

    Raises:
        HTTPException 404: No key stored for this user yet.
        HTTPException 500: Decryption failed (corrupted data or key rotation).
    """
    supabase = get_supabase()

    try:
        result = await run_db(
            supabase.table("api_keys")
            .select("encrypted_key")
            .eq("user_id", user_id)
            .single()
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API key found. Please add your Gemini API key in Settings.",
        )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API key found. Please add your Gemini API key in Settings.",
        )

    try:
        return decrypt_api_key(result.data["encrypted_key"])
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt API key. Please re-save your key in Settings.",
        )


# ── Helper: build the Gemini request payload ──────────────────────────────

def _build_gemini_payload(
    system_prompt: str,
    user_message: str,
    stream: bool = True,
) -> dict:
    """
    Builds the request body for the Gemini generateContent endpoint.
    Gemini uses a different structure from OpenAI — system instructions
    are a separate top-level field, not a message in the array.
    """
    return {
        "system_instruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_message}]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 4096,
            "temperature": 1.0,       # Higher = more varied word choices (better for humanizing)
            "topP": 0.95,
            "topK": 64,
        },
        "safetySettings": [
            # Disable overly aggressive safety filters that would block
            # rewriting of academic or edgy-but-legitimate content
            {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
        ],
    }


# ── Streaming response generator ──────────────────────────────────────────

async def _stream_gemini(
    api_key: str,
    system_prompt: str,
    user_message: str,
):
    """
    Async generator that streams Gemini's response chunk by chunk.

    Gemini's streaming endpoint returns Server-Sent Events (SSE).
    Each event is a JSON object containing a partial candidate text.
    We extract the text delta from each event and yield it so FastAPI
    can forward it to the browser as it arrives.

    Yields:
        str — each chunk formatted as SSE: "data: <json>\n\n"
               The frontend's EventSource or fetch reader parses these.
    """
    url = (
        f"{GEMINI_BASE_URL}/models/{GEMINI_MODEL}"
        f":streamGenerateContent?alt=sse&key={api_key}"
    )
    payload = _build_gemini_payload(system_prompt, user_message, stream=True)

    async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
        async with client.stream("POST", url, json=payload) as response:

            # Surface Gemini API errors before streaming starts
            if response.status_code != 200:
                error_body = await response.aread()
                try:
                    error_json = json.loads(error_body)
                    error_msg = error_json.get("error", {}).get("message", "Unknown Gemini error")
                except Exception:
                    error_msg = f"Gemini API returned HTTP {response.status_code}"

                # Map Gemini errors to meaningful messages for the user
                if response.status_code == 400:
                    detail = f"Invalid request: {error_msg}"
                elif response.status_code == 403:
                    detail = "Gemini API key is invalid or lacks permission. Check your key in Settings."
                elif response.status_code == 429:
                    detail = "Your Gemini API key has hit its rate limit. Wait a minute and try again."
                elif response.status_code == 500:
                    detail = "Gemini API internal error. Try again shortly."
                else:
                    detail = error_msg

                # Yield an error event so the frontend knows what happened
                yield f"data: {json.dumps({'error': detail})}\n\n"
                return

            # Stream the response line by line
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue

                raw = line[6:]  # Strip "data: " prefix

                # Gemini signals end of stream with "[DONE]"
                if raw.strip() == "[DONE]":
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    return

                try:
                    chunk = json.loads(raw)
                    # Navigate Gemini's nested response structure
                    candidates = chunk.get("candidates", [])
                    if not candidates:
                        continue

                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        continue

                    text_delta = parts[0].get("text", "")
                    if text_delta:
                        yield f"data: {json.dumps({'text': text_delta})}\n\n"

                    # Check for finish reason
                    finish_reason = candidates[0].get("finishReason", "")
                    if finish_reason and finish_reason != "STOP":
                        # SAFETY, RECITATION, etc.
                        yield f"data: {json.dumps({'warning': f'Stopped: {finish_reason}'})}\n\n"

                except json.JSONDecodeError:
                    continue  # Malformed chunk — skip and continue


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("/humanize")
async def humanize(
    request: HumanizeRequest,
    user: UserInfo = Depends(get_current_user),
):
    """
    Main humanize endpoint — streams the rewritten text back to the client.

    POST /api/humanize
    Headers: Authorization: Bearer <token>
    Body: HumanizeRequest JSON

    Returns: StreamingResponse (SSE)
    Each event: data: {"text": "<chunk>"}
    Final event: data: {"done": true}
    Error event: data: {"error": "<message>"}
    """
    # ── 1. Rate limit check ───────────────────────────────────────
    await check_and_increment(user.user_id)

    # ── 2. Fetch and decrypt the user's Gemini key ────────────────
    gemini_key = await _get_user_gemini_key(user.user_id)

    # ── 3. Build the system prompt ────────────────────────────────
    system_prompt = build_system_prompt(
        profile=request.profile,
        strength=request.strength,
    )
    user_message = build_user_message(request.text)

    # ── 4. Stream Gemini response back to client ──────────────────
    return StreamingResponse(
        _stream_gemini(gemini_key, system_prompt, user_message),
        media_type="text/event-stream",
        headers={
            # Prevent buffering — essential for streaming to work
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            # Expose rate limit info in response headers
            "X-RateLimit-Limit-Hour": "20",
        },
    )


@router.get("/humanize/usage")
async def get_usage_stats(user: UserInfo = Depends(get_current_user)):
    """
    Returns the current user's rate limit usage without consuming a request.
    Frontend uses this to show the "X rewrites remaining" indicator.

    GET /api/humanize/usage
    Headers: Authorization: Bearer <token>
    """
    return await get_usage(user.user_id)


# ── API key management ─────────────────────────────────────────────────────

@router.post("/key", response_model=SuccessResponse)
async def save_api_key(
    request: SaveKeyRequest,
    user: UserInfo = Depends(get_current_user),
):
    """
    Encrypts and stores (or updates) the user's Gemini API key.

    The key is encrypted with AES-256-GCM before storage.
    Uses upsert so re-saving a key overwrites the old one.

    POST /api/key
    Headers: Authorization: Bearer <token>
    Body: {"api_key": "AIzaSy..."}
    """
    supabase = get_supabase()

    encrypted = encrypt_api_key(request.api_key)

    try:
        await run_db(
            supabase.table("api_keys").upsert(
                {
                    "user_id":       user.user_id,
                    "encrypted_key": encrypted,
                },
                on_conflict="user_id",
            )
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save API key: {str(e)}",
        )

    return SuccessResponse(message="API key saved successfully.")


@router.get("/key/status", response_model=KeyStatusResponse)
async def get_key_status(user: UserInfo = Depends(get_current_user)):
    """
    Checks whether the user has a stored API key without exposing it.
    Frontend uses this to show "Key configured ✓" or "Add your key" prompt.

    GET /api/key/status
    Headers: Authorization: Bearer <token>
    """
    supabase = get_supabase()

    try:
        result = await run_db(
            supabase.table("api_keys")
            .select("user_id")
            .eq("user_id", user.user_id)
        )
        has_key = bool(result.data)
    except Exception:
        has_key = False

    return KeyStatusResponse(has_key=has_key)


@router.delete("/key", response_model=SuccessResponse)
async def delete_api_key(user: UserInfo = Depends(get_current_user)):
    """
    Deletes the user's stored API key entirely.

    DELETE /api/key
    Headers: Authorization: Bearer <token>
    """
    supabase = get_supabase()

    try:
        await run_db(
            supabase.table("api_keys").delete().eq("user_id", user.user_id)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete API key: {str(e)}",
        )

    return SuccessResponse(message="API key deleted.")
