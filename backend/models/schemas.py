"""
models/schemas.py

Pydantic models for all API request bodies and response shapes.
Defining these centrally means every route gets automatic validation
and clear error messages when a request is malformed.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────────────────

class HumanizeStrength(str, Enum):
    light      = "light"
    medium     = "medium"
    aggressive = "aggressive"


# ── Style profile ──────────────────────────────────────────────────────────

class StyleProfile(BaseModel):
    """
    The statistical fingerprint extracted from a user's writing samples.
    Computed client-side in profile.js and stored server-side per user.
    """
    # Sentence-level features
    avg_sentence_length: float = Field(
        default=15.0,
        description="Average words per sentence"
    )
    burstiness: float = Field(
        default=5.0,
        description="Std deviation of sentence lengths — higher = more human-like variation"
    )
    fragment_rate: float = Field(
        default=0.05,
        description="Fraction of sentences under 5 words"
    )

    # Word-level features
    contraction_rate: float = Field(
        default=0.03,
        description="Fraction of words that are contractions"
    )
    first_person_rate: float = Field(
        default=0.02,
        description="Fraction of words that are first-person pronouns"
    )
    avg_word_length: float = Field(
        default=4.5,
        description="Average characters per word"
    )
    exclamation_rate: float = Field(
        default=0.02,
        description="Exclamation marks per sentence"
    )

    # Punctuation habits
    uses_dash: bool = Field(
        default=False,
        description="Whether the user uses em dashes"
    )
    uses_ellipsis: bool = Field(
        default=False,
        description="Whether the user uses ellipsis"
    )
    uses_semicolon: bool = Field(
        default=False,
        description="Whether the user uses semicolons"
    )

    # Vocabulary
    distinctive_words: list[str] = Field(
        default_factory=list,
        description="Words that appear 2-6 times — characteristic but not noise"
    )
    found_hedges: list[str] = Field(
        default_factory=list,
        description="Natural hedge phrases detected in samples (e.g. 'i think', 'honestly')"
    )
    found_fillers: list[str] = Field(
        default_factory=list,
        description="AI filler words found in samples — flagged for exclusion"
    )

    # Metadata
    sample_count: int = Field(
        default=0,
        description="Number of writing samples used to build this profile"
    )
    word_count: int = Field(
        default=0,
        description="Total words across all samples"
    )


# ── Humanize route ─────────────────────────────────────────────────────────

class HumanizeRequest(BaseModel):
    """Request body for POST /api/humanize"""

    text: str = Field(
        ...,
        min_length=10,
        max_length=8000,
        description="The AI-generated text to rewrite"
    )
    strength: HumanizeStrength = Field(
        default=HumanizeStrength.medium,
        description="How aggressively to rewrite"
    )
    profile: Optional[StyleProfile] = Field(
        default=None,
        description="User's style profile — if None, generic humanization is used"
    )

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Text cannot be blank or whitespace only.")
        return v.strip()


class HumanizeResponse(BaseModel):
    """Response body for POST /api/humanize (non-streaming fallback)"""

    output: str
    input_word_count: int
    output_word_count: int
    strength: HumanizeStrength
    profile_used: bool


# ── Profile route ──────────────────────────────────────────────────────────

class SaveProfileRequest(BaseModel):
    """Request body for POST /api/profile"""
    profile: StyleProfile


class ProfileResponse(BaseModel):
    """Response body for GET /api/profile"""
    profile: Optional[StyleProfile] = None
    exists: bool


# ── API key route ──────────────────────────────────────────────────────────

class SaveKeyRequest(BaseModel):
    """Request body for POST /api/key"""

    api_key: str = Field(
        ...,
        min_length=10,
        description="The user's Gemini API key — will be encrypted before storage"
    )

    @field_validator("api_key")
    @classmethod
    def key_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("API key cannot be blank.")
        return v.strip()


class KeyStatusResponse(BaseModel):
    """Response for GET /api/key/status — confirms a key is stored without exposing it"""
    has_key: bool


# ── Auth / user ────────────────────────────────────────────────────────────

class UserInfo(BaseModel):
    """Extracted from a validated Supabase JWT — passed between route handlers"""
    user_id: str
    email: Optional[str] = None


# ── Generic responses ──────────────────────────────────────────────────────

class SuccessResponse(BaseModel):
    success: bool = True
    message: str = "OK"


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[str] = None
