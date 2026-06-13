"""
core/encryption.py

AES-256-GCM encryption for Gemini API keys stored in Supabase.

Why AES-256-GCM:
- AES-256: 256-bit key, unbroken, industry standard
- GCM mode: authenticated encryption — detects if the ciphertext
  was tampered with (unlike CBC which only encrypts, doesn't verify)
- Each encryption generates a fresh random 12-byte nonce so the same
  plaintext never produces the same ciphertext twice

Storage format (all base64, stored as single string in DB):
    <nonce_b64>:<ciphertext_b64>:<tag_b64>

The tag is the GCM authentication tag — decryption fails immediately
if any byte of the stored value was modified.
"""

import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from core.config import get_settings


def _get_key() -> bytes:
    """
    Derives the 32-byte AES key from the hex ENCRYPTION_SECRET env var.
    Called fresh each time so key material is never cached in memory
    longer than needed.
    """
    settings = get_settings()
    return bytes.fromhex(settings.encryption_secret)


def encrypt_api_key(plaintext_key: str) -> str:
    """
    Encrypts a Gemini API key for storage in Supabase.

    Args:
        plaintext_key: The raw API key string (e.g. "AIzaSy...")

    Returns:
        A colon-separated base64 string: "<nonce>:<ciphertext>:<tag>"
        Safe to store directly in a text column.

    Example:
        stored = encrypt_api_key("AIzaSyD-9tSrke72...")
        # "dGVzdA==:c2VjcmV0:dGFn..."
    """
    key = _get_key()
    aesgcm = AESGCM(key)

    # Fresh 12-byte nonce per encryption — never reuse
    nonce = os.urandom(12)

    # GCM returns ciphertext + 16-byte auth tag appended
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext_key.encode("utf-8"), None)

    # Split: last 16 bytes are the tag, rest is ciphertext
    ciphertext = ciphertext_with_tag[:-16]
    tag = ciphertext_with_tag[-16:]

    # Encode all three parts to base64 and join with colons
    nonce_b64      = base64.b64encode(nonce).decode()
    ciphertext_b64 = base64.b64encode(ciphertext).decode()
    tag_b64        = base64.b64encode(tag).decode()

    return f"{nonce_b64}:{ciphertext_b64}:{tag_b64}"


def decrypt_api_key(stored_value: str) -> str:
    """
    Decrypts a stored Gemini API key.

    Args:
        stored_value: The colon-separated base64 string from Supabase.

    Returns:
        The original plaintext API key string.

    Raises:
        ValueError: If the format is invalid.
        cryptography.exceptions.InvalidTag: If the ciphertext was tampered
            with or the wrong key is being used. This exception propagates
            intentionally — callers should treat it as an auth failure.

    Example:
        key = decrypt_api_key("dGVzdA==:c2VjcmV0:dGFn...")
        # "AIzaSyD-9tSrke72..."
    """
    parts = stored_value.split(":")
    if len(parts) != 3:
        raise ValueError(
            f"Invalid encrypted key format — expected 3 colon-separated parts, "
            f"got {len(parts)}. The stored value may be corrupted."
        )

    nonce_b64, ciphertext_b64, tag_b64 = parts

    nonce      = base64.b64decode(nonce_b64)
    ciphertext = base64.b64decode(ciphertext_b64)
    tag        = base64.b64decode(tag_b64)

    key = _get_key()
    aesgcm = AESGCM(key)

    # GCM expects ciphertext + tag concatenated
    ciphertext_with_tag = ciphertext + tag

    # Will raise InvalidTag if tampered or wrong key — let it propagate
    plaintext_bytes = aesgcm.decrypt(nonce, ciphertext_with_tag, None)

    return plaintext_bytes.decode("utf-8")


def rotate_api_key(stored_value: str, new_plaintext_key: str) -> str:
    """
    Convenience function: decrypt the old key (to verify it was valid),
    then encrypt the new one. Used when a user updates their API key.

    Args:
        stored_value:     The current encrypted value from Supabase.
        new_plaintext_key: The new raw API key to store.

    Returns:
        New encrypted string ready for storage.

    Raises:
        Same exceptions as decrypt_api_key if the existing value is invalid.
    """
    # Verify we can still decrypt the old key before overwriting
    decrypt_api_key(stored_value)
    return encrypt_api_key(new_plaintext_key)
