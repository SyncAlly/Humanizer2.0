"""
core/config.py

Single source of truth for all environment variables and shared clients.
Every other module imports from here — nothing reads os.environ directly.
"""

import os
from functools import lru_cache
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


class Settings:
    """
    Validates and exposes all required environment variables.
    Raises a clear error at startup if anything is missing —
    better to crash immediately than to fail silently mid-request.
    """

    def __init__(self):
        # ── Supabase ──────────────────────────────────────────────
        self.supabase_url: str = self._require("SUPABASE_URL")
        self.supabase_anon_key: str = self._require("SUPABASE_ANON_KEY")
        self.supabase_service_key: str = self._require("SUPABASE_SERVICE_KEY")
        self.supabase_jwt_secret: str = self._require("SUPABASE_JWT_SECRET")

        # ── Encryption ────────────────────────────────────────────
        # Must be a 64-char hex string (32 bytes).
        # Generate: python -c "import secrets; print(secrets.token_hex(32))"
        raw_secret = self._require("ENCRYPTION_SECRET")
        if len(raw_secret) != 64:
            raise ValueError(
                "ENCRYPTION_SECRET must be a 64-character hex string (32 bytes). "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        self.encryption_secret: str = raw_secret

        # ── Gemini (eval only) ────────────────────────────────────
        # Optional — only needed to run eval/benchmark.py.
        # Users supply their own keys through the app UI.
        self.gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

        # ── CORS ──────────────────────────────────────────────────
        raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
        self.allowed_origins: list[str] = [
            o.strip() for o in raw_origins.split(",") if o.strip()
        ]

        # ── App ───────────────────────────────────────────────────
        self.port: int = int(os.getenv("PORT", "8000"))
        self.environment: str = os.getenv("ENVIRONMENT", "development")
        self.is_production: bool = self.environment == "production"

    @staticmethod
    def _require(key: str) -> str:
        """Read a required env var; raise immediately if missing."""
        value = os.getenv(key)
        if not value:
            raise EnvironmentError(
                f"Required environment variable '{key}' is not set. "
                f"Check your .env file against .env.example."
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    Using lru_cache means Settings() is only called once per process —
    safe to import get_settings() anywhere without re-reading the env.
    """
    return Settings()


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """
    Returns a cached Supabase client using the SERVICE ROLE key.

    The service role key bypasses Row Level Security — it is for
    backend use only. Never pass this client or key to the frontend.

    For operations that should respect RLS (e.g. verifying a user
    can only read their own row), pass the user's JWT when calling
    Supabase from the backend instead.
    """
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_key)


@lru_cache(maxsize=1)
def get_supabase_anon() -> Client:
    """
    Returns a Supabase client using the ANON key.
    Used for verifying JWTs issued to users — does not bypass RLS.
    """
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_anon_key)
