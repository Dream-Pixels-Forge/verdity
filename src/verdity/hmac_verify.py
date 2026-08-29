"""
HMAC-SHA256 webhook signature verification utility.

Non-negotiable constraint #1: Every inbound webhook is HMAC-SHA256 verified
over the raw body with constant-time comparison before any parsing.
No endpoint skips this, including in dev/staging. No bypass flag.
"""

from __future__ import annotations

import hmac
import hashlib


def verify_signature(
    secret: bytes,
    raw_body: bytes,
    signature_header: str,
) -> bool:
    """
    Verify an X-Hub-Signature-256 header against raw request body.

    Uses hmac.compare_digest for constant-time comparison to prevent
    timing side-channel attacks.

    Returns True only when the signature is valid.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.HMAC(secret, raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


def verify_with_rotation(
    *,
    secret_current: bytes,
    secret_previous: bytes,
    raw_body: bytes,
    signature_header: str,
) -> tuple[bool, str]:
    """
    Verify a webhook signature allowing for secret rotation.

    Returns (verified: bool, matched_secret: str) where matched_secret is
    'current' or 'previous'. If neither matches, returns (False, '').

    Supports the dual-secret rotation window (API spec §1.5).
    """
    if verify_signature(secret_current, raw_body, signature_header):
        return True, "current"
    if secret_previous and verify_signature(secret_previous, raw_body, signature_header):
        return True, "previous"
    return False, ""


def compute_signature(secret: bytes, raw_body: bytes) -> str:
    """Helper for tests — compute what the signature header should be."""
    digest = hmac.HMAC(secret, raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
