"""
main.py

FastAPI application entry point.

Responsibilities:
- Create the FastAPI app instance
- Configure CORS (which frontend origins can call the backend)
- Register all routers
- Run startup checks (env vars, Supabase connection)
- Expose a health check endpoint for Railway deployment monitoring

Run locally:
    cd backend
    uvicorn main:app --reload --port 8000

Deploy to Railway:
    Railway auto-detects the Procfile or uses:
    uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.config import get_settings, get_supabase
from routers import auth, humanize, profile


# ── Startup / shutdown lifecycle ───────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once at startup before accepting requests, and once at shutdown.
    Used to validate configuration early so the server fails fast with a
    clear error instead of crashing on the first real request.
    """
    # ── Startup ──────────────────────────────────────────────────
    print("── Humanizer API starting up ──")

    try:
        settings = get_settings()
        print(f"  Environment : {settings.environment}")
        print(f"  Allowed origins : {settings.allowed_origins}")
    except EnvironmentError as e:
        print(f"\n[FATAL] Configuration error: {e}")
        print("Fix your .env file and restart.\n")
        sys.exit(1)

    # Verify Supabase connection
    try:
        supabase = get_supabase()
        # Lightweight query to confirm DB is reachable
        supabase.table("profiles").select("user_id").limit(1).execute()
        print("  Supabase     : connected ✓")
    except Exception as e:
        print(f"\n[FATAL] Supabase connection failed: {e}")
        print("Check SUPABASE_URL and SUPABASE_SERVICE_KEY in your .env file.\n")
        sys.exit(1)

    print("── Ready to accept requests ──\n")

    yield  # Application runs here

    # ── Shutdown ─────────────────────────────────────────────────
    print("\n── Humanizer API shutting down ──")


# ── App instance ───────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Humanizer API",
        description=(
            "Backend for the Humanizer web app. "
            "Proxies Gemini API calls server-side so API keys never touch the browser. "
            "Manages per-user style profiles, encrypted key storage, and rate limiting."
        ),
        version="1.0.0",
        # Disable automatic /docs and /redoc in production —
        # no need to expose API structure publicly
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        lifespan=lifespan,
    )

    # ── CORS ─────────────────────────────────────────────────────
    # Only the origins listed in ALLOWED_ORIGINS can call this API.
    # In development: http://localhost:3000
    # In production:  your GitHub Pages / Netlify URL
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,         # Required for Authorization header
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-RateLimit-Limit-Hour"],
    )

    # ── Routers ───────────────────────────────────────────────────
    app.include_router(auth.router)
    app.include_router(humanize.router)
    app.include_router(profile.router)

    # ── Global exception handler ──────────────────────────────────
    # Catches any unhandled exception and returns a clean JSON error
    # instead of leaking a Python stack trace to the client.
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        # Always log the real error server-side
        print(f"[ERROR] Unhandled exception on {request.method} {request.url}: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Internal server error",
                "detail": str(exc) if not settings.is_production else None,
            },
        )

    # ── Health check ──────────────────────────────────────────────
    @app.get("/health")
    async def health():
        """
        Used by Railway to confirm the app is running.
        Also useful to ping from the frontend on startup to confirm
        the backend is reachable before showing the main UI.

        GET /health → 200 {"status": "ok", "environment": "..."}
        """
        settings = get_settings()
        return {
            "status": "ok",
            "environment": settings.environment,
        }

    # ── Root ──────────────────────────────────────────────────────
    @app.get("/")
    async def root():
        return {
            "name":    "Humanizer API",
            "version": "1.0.0",
            "docs":    "/docs" if not settings.is_production else "disabled in production",
        }

    return app


app = create_app()


# ── Entry point for local development ─────────────────────────────────────
# Railway and Render use: uvicorn main:app --host 0.0.0.0 --port $PORT
# For local dev with auto-reload: uvicorn main:app --reload --port 8000

if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=not settings.is_production,
        log_level="info",
    )
