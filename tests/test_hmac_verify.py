"""
Tests for HMAC signature verification (non-negotiable constraint #1).
"""

from __future__ import annotations



from verdity.hmac_verify import (
    compute_signature,
    verify_signature,
    verify_with_rotation,
)


SECRET = b"my-webhook-secret"
BODY = b'{"action":"opened","repository":{"id":1}}'


class TestVerifySignature:
    def test_valid_signature(self):
        sig = compute_signature(SECRET, BODY)
        assert verify_signature(SECRET, BODY, sig) is True

    def test_invalid_signature(self):
        assert verify_signature(SECRET, BODY, "sha256=0000000000000000000000000000000000000000000000000000000000000000") is False

    def test_missing_header(self):
        assert verify_signature(SECRET, BODY, "") is False

    def test_header_without_sha256_prefix(self):
        assert verify_signature(SECRET, BODY, "hmac=abc123") is False

    def test_tampered_body_rejected(self):
        sig = compute_signature(SECRET, BODY)
        # tamper with body
        assert verify_signature(SECRET, b"tampered-body", sig) is False

    def test_constant_time_comparison_used(self):
        """
        Sanity check: verify_signature uses hmac.compare_digest (constant-time).
        We can't easily test timing, but we verify the function exists and works.
        """
        import inspect
        source = inspect.getsource(verify_signature)
        assert "compare_digest" in source, "Must use hmac.compare_digest for constant-time comparison"


class TestVerifyWithRotation:
    def test_current_secret_matches(self):
        sig = compute_signature(SECRET, BODY)
        verified, matched = verify_with_rotation(
            secret_current=SECRET,
            secret_previous=b"previous-secret",
            raw_body=BODY,
            signature_header=sig,
        )
        assert verified is True
        assert matched == "current"

    def test_previous_secret_matches(self):
        sig = compute_signature(b"previous-secret", BODY)
        verified, matched = verify_with_rotation(
            secret_current=SECRET,
            secret_previous=b"previous-secret",
            raw_body=BODY,
            signature_header=sig,
        )
        assert verified is True
        assert matched == "previous"

    def test_neither_secret_matches(self):
        sig = compute_signature(b"unknown-secret", BODY)
        verified, matched = verify_with_rotation(
            secret_current=SECRET,
            secret_previous=b"previous-secret",
            raw_body=BODY,
            signature_header=sig,
        )
        assert verified is False
        assert matched == ""

    def test_empty_previous_secret(self):
        """During non-rotation, previous is empty — only current accepted."""
        sig = compute_signature(SECRET, BODY)
        verified, matched = verify_with_rotation(
            secret_current=SECRET,
            secret_previous=b"",
            raw_body=BODY,
            signature_header=sig,
        )
        assert verified is True
        assert matched == "current"


class TestComputeSignature:
    def test_deterministic(self):
        s1 = compute_signature(SECRET, BODY)
        s2 = compute_signature(SECRET, BODY)
        assert s1 == s2
        assert s1.startswith("sha256=")
