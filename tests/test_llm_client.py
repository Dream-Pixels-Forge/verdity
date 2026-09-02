"""
Tests for LLM Client module (Phase 12).

Gate test: test_gate_phase12_llm — security agent with use_llm=True finds
at least one issue that regex-only misses on a test diff containing a logic flaw.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from verdity.llm_client import (
    LLMClient,
    LLMResponse,
    _extract_json_from_text,
    _validate_json_against_schema,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _mock_response(content: str, model: str = "gpt-4o-mini") -> dict:
    """Build a mock OpenAI chat completion response."""
    return {
        "choices": [{"message": {"content": content}}],
        "model": model,
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": len(content) // 4,
        },
    }


# ── Gate Test ─────────────────────────────────────────────────────────


class TestGatePhase12:
    """Phase 12 gate: LLM-enhanced security scan finds issues regex misses."""

    async def test_gate_phase12_llm(self):
        """
        Gate criterion: Security agent with use_llm=True finds at least one
        issue that regex-only misses on a test diff containing a logic flaw.

        Test diff: a login endpoint that hashes passwords with MD5 (weak hash —
        regex catches this) AND has an auth bypass via missing token validation
        (logic flaw — regex CANNOT catch this).
        """
        from verdity.agents.security import SecurityAgent
        from verdity.schemas import (
            Severity,
            SpecialistContext,
        )

        # Diff with: (1) MD5 hash — regex catches, (2) auth bypass — logic flaw
        logic_flaw_diff = '''--- a/auth/login.py
+++ b/auth/login.py
@@ -10,6 +10,12 @@
 import hashlib
+import uuid

 def login(username, password):
     password_hash = hashlib.md5(password.encode()).hexdigest()
-    user = db.query("SELECT * FROM users WHERE username = ? AND password = ?", username, password_hash)
+    # BUG: Missing token validation — any request with valid username bypasses auth
+    user = db.query("SELECT * FROM users WHERE username = ?", username)
+    if user:
+        token = str(uuid.uuid4())
+        return {"token": token, "user": user}
     return None
'''

        ctx = SpecialistContext(
            review_run_id=uuid.uuid4(),
            repo_owner="test-owner",
            repo_name="test-repo",
            base_sha="abc123",
            head_sha="def456",
            diff_files=[{
                "path": "auth/login.py",
                "content": logic_flaw_diff,
                "additions": logic_flaw_diff,
            }],
        )

        # Mock LLM response that catches the auth bypass logic flaw
        llm_finding = json.dumps([{
            "summary": "Auth bypass: missing token validation",
            "severity": "critical",
            "file": "auth/login.py",
            "line_start": 14,
            "explanation": (
                "The login function queries by username only, without validating the password. "
                "This allows any request with a valid username to authenticate and receive a token. "
                "The original code checked both username AND password, but the new code only checks username."
            ),
            "suggested_fix": "Query with both username and password_hash, not just username",
        }])

        agent = SecurityAgent()

        # Mock the LLM client
        mock_llm_client = MagicMock()
        mock_llm_client.enabled = True
        mock_llm_client.complete = AsyncMock(return_value=LLMResponse(
            content=llm_finding,
            input_tokens=200,
            output_tokens=100,
            model="gpt-4o",
            cost_usd=0.003,
        ))
        ctx.llm_client = mock_llm_client

        # Run with use_llm=False (regex only)
        mock_index = MagicMock()
        mock_index.search_by_text = AsyncMock(return_value=[])
        mock_te = MagicMock()
        mock_te.record_call = AsyncMock(return_value=0.0)
        mock_audit = MagicMock()
        mock_audit.append = AsyncMock()


        findings_no_llm = await agent._scan(ctx, mock_index, use_llm=False)

        # Run with use_llm=True (regex + LLM)
        findings_with_llm = await agent._scan(ctx, mock_index, use_llm=True)

        # Regex catches MD5 (at least 1 finding)
        assert len(findings_no_llm) >= 1, "Regex should catch MD5 weak hash"

        # LLM finds the auth bypass that regex cannot catch
        llm_only_findings = [
            f for f in findings_with_llm
            if f.summary.startswith("[LLM]")
        ]
        assert len(llm_only_findings) >= 1, (
            "LLM should find the auth bypass logic flaw that regex misses"
        )

        # Verify the LLM finding is about the auth bypass
        auth_bypass = llm_only_findings[0]
        assert "auth bypass" in auth_bypass.summary.lower() or "token" in auth_bypass.explanation.lower()
        assert auth_bypass.severity in (Severity.CRITICAL, Severity.HIGH)


# ── JSON Extraction ───────────────────────────────────────────────────


class TestExtractJson:
    """Tests for _extract_json_from_text helper."""

    def test_raw_json(self):
        result = _extract_json_from_text('{"key": "value"}')
        assert result == '{"key": "value"}'

    def test_json_in_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        result = _extract_json_from_text(text)
        assert result == '{"key": "value"}'

    def test_json_in_code_block_no_lang(self):
        text = '```\n{"key": "value"}\n```'
        result = _extract_json_from_text(text)
        assert result == '{"key": "value"}'

    def test_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"key": "value"}\nDone.'
        result = _extract_json_from_text(text)
        assert '"key": "value"' in result

    def test_empty_text(self):
        result = _extract_json_from_text("")
        assert result == ""


# ── Schema Validation ─────────────────────────────────────────────────


class TestValidateSchema:
    """Tests for _validate_json_against_schema helper."""

    def test_valid_schema(self):
        schema = {
            "required": ["name", "severity"],
            "properties": {
                "name": {"type": "string"},
                "severity": {"type": "string"},
            },
        }
        errors = _validate_json_against_schema({"name": "test", "severity": "high"}, schema)
        assert errors == []

    def test_missing_required_field(self):
        schema = {
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        errors = _validate_json_against_schema({}, schema)
        assert len(errors) == 1
        assert "Missing required field: name" in errors[0]

    def test_wrong_type(self):
        schema = {
            "required": ["count"],
            "properties": {"count": {"type": "integer"}},
        }
        errors = _validate_json_against_schema({"count": "not_int"}, schema)
        assert len(errors) == 1
        assert "should be integer" in errors[0]

    def test_array_type(self):
        schema = {
            "required": ["items"],
            "properties": {"items": {"type": "array"}},
        }
        errors = _validate_json_against_schema({"items": [1, 2, 3]}, schema)
        assert errors == []

    def test_array_type_wrong(self):
        schema = {
            "required": ["items"],
            "properties": {"items": {"type": "array"}},
        }
        errors = _validate_json_against_schema({"items": "not_array"}, schema)
        assert len(errors) == 1


# ── LLMClient ─────────────────────────────────────────────────────────


class TestLLMClient:
    """Tests for the LLMClient class."""

    def test_enabled_with_api_key(self):
        client = LLMClient(api_key="test-key")
        assert client.enabled is True

    def test_disabled_without_api_key(self):
        client = LLMClient()
        assert client.enabled is False

    def test_disabled_with_empty_key(self):
        client = LLMClient(api_key="")
        assert client.enabled is False

    async def test_complete_raises_without_api_key(self):
        client = LLMClient()
        with pytest.raises(RuntimeError, match="no API key configured"):
            await client.complete(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "test"}],
            )

    async def test_complete_success(self):
        client = LLMClient(api_key="test-key")
        mock_response = _mock_response('{"result": "ok"}')

        with patch("verdity.llm_client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.complete(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "test"}],
            )

            assert isinstance(result, LLMResponse)
            assert result.content == '{"result": "ok"}'
            assert result.model == "gpt-4o-mini"
            assert result.input_tokens > 0
            assert result.output_tokens > 0

    async def test_complete_metered_through_token_economics(self):
        te = MagicMock()
        te.record_call = AsyncMock(return_value=0.0)
        review_run_id = uuid.uuid4()

        client = LLMClient(
            api_key="test-key",
            token_economics=te,
            review_run_id=str(review_run_id),
            agent_name="test-agent",
            repo_owner="owner",
            repo_name="repo",
        )
        mock_response = _mock_response("test response")

        with patch("verdity.llm_client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.complete(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "test"}],
            )

            # Verify token economics was called
            te.record_call.assert_called_once()
            call_kwargs = te.record_call.call_args.kwargs
            assert call_kwargs["agent_name"] == "test-agent"
            assert call_kwargs["model"] == "gpt-4o-mini"
            assert call_kwargs["repo_owner"] == "owner"

    async def test_complete_structured_success(self):
        client = LLMClient(api_key="test-key")
        finding_json = json.dumps([{
            "summary": "Test finding",
            "severity": "high",
            "file": "test.py",
            "line_start": 1,
            "explanation": "Test explanation",
            "suggested_fix": "Fix it",
        }])
        mock_response = _mock_response(finding_json)

        with patch("verdity.llm_client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            schema = {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["summary", "severity"],
                    "properties": {
                        "summary": {"type": "string"},
                        "severity": {"type": "string"},
                    },
                },
            }
            result = await client.complete_structured(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "test"}],
                schema=schema,
            )

            assert isinstance(result, (dict, list))

    async def test_complete_structured_retries_on_bad_json(self):
        client = LLMClient(api_key="test-key")

        # First call returns invalid JSON, second returns valid
        bad_response = _mock_response("This is not JSON at all")
        good_response = _mock_response(json.dumps({"name": "test"}))

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.json.return_value = bad_response
            else:
                resp.json.return_value = good_response
            resp.raise_for_status = MagicMock()
            return resp

        with patch("verdity.llm_client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = mock_post
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            schema = {
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            }
            result = await client.complete_structured(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "test"}],
                schema=schema,
                max_retries=2,
            )

            assert result == {"name": "test"}
            assert call_count == 2

    async def test_complete_structured_raises_after_max_retries(self):
        client = LLMClient(api_key="test-key")
        bad_response = _mock_response("Not JSON at all")

        with patch("verdity.llm_client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = bad_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            schema = {"required": ["name"], "properties": {"name": {"type": "string"}}}
            with pytest.raises(ValueError, match="Failed to get valid structured response"):
                await client.complete_structured(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "test"}],
                    schema=schema,
                    max_retries=1,
                )

    async def test_complete_http_error(self):
        client = LLMClient(api_key="test-key")

        import httpx

        with patch("verdity.llm_client.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Rate limited",
                request=MagicMock(),
                response=mock_resp,
            )
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(RuntimeError, match="LLM API error"):
                await client.complete(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "test"}],
                )


# ── Config Integration ────────────────────────────────────────────────


class TestConfigIntegration:
    """Tests for LLM config settings."""

    def test_llm_config_defaults(self):
        # Verify LLM settings exist with correct defaults
        import os

        from verdity.config import Settings
        os.environ["VERDITY_WEBHOOK_HMAC_SECRET"] = "test-secret"
        os.environ["VERDITY_GITHUB_APP_ID"] = "12345"
        os.environ["VERDITY_GITHUB_APP_INSTALLATION_ID"] = "67890"
        os.environ["VERDITY_GITHUB_APP_PRIVATE_KEY"] = "test-key"

        try:
            settings = Settings()
            assert settings.llm_enabled is False
            assert settings.llm_model == "gpt-4o-mini"
            assert settings.llm_security_model == "gpt-4o"
            assert settings.llm_temperature == 0.0
            assert settings.llm_max_tokens == 4096
        finally:
            for key in [
                "VERDITY_WEBHOOK_HMAC_SECRET",
                "VERDITY_GITHUB_APP_ID",
                "VERDITY_GITHUB_APP_INSTALLATION_ID",
                "VERDITY_GITHUB_APP_PRIVATE_KEY",
            ]:
                os.environ.pop(key, None)


# ── LLMResponse ───────────────────────────────────────────────────────


class TestLLMResponse:
    """Tests for the LLMResponse dataclass."""

    def test_response_fields(self):
        resp = LLMResponse(
            content="test content",
            input_tokens=100,
            output_tokens=50,
            model="gpt-4o-mini",
            cost_usd=0.001,
        )
        assert resp.content == "test content"
        assert resp.input_tokens == 100
        assert resp.output_tokens == 50
        assert resp.model == "gpt-4o-mini"
        assert resp.cost_usd == 0.001

    def test_response_is_dataclass(self):
        from dataclasses import fields

        resp = LLMResponse(
            content="",
            input_tokens=0,
            output_tokens=0,
            model="",
            cost_usd=0.0,
        )
        field_names = {f.name for f in fields(resp)}
        assert field_names == {"content", "input_tokens", "output_tokens", "model", "cost_usd"}


# ── Schema Validation Branches ───────────────────────────────────────


class TestSchemaValidationBranches:
    """Cover all type branches in _validate_json_against_schema."""

    def test_string_field_with_non_string(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        errors = _validate_json_against_schema({"name": 123}, schema)
        assert any("string" in e for e in errors)

    def test_number_field_with_non_number(self):
        schema = {"type": "object", "properties": {"count": {"type": "number"}}}
        errors = _validate_json_against_schema({"count": "not-a-number"}, schema)
        assert any("number" in e for e in errors)

    def test_boolean_field_with_non_boolean(self):
        schema = {"type": "object", "properties": {"flag": {"type": "boolean"}}}
        errors = _validate_json_against_schema({"flag": "yes"}, schema)
        assert any("boolean" in e for e in errors)

    def test_integer_field_with_non_integer(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        errors = _validate_json_against_schema({"n": 1.5}, schema)
        assert any("integer" in e for e in errors)

    def test_array_field_with_non_array(self):
        schema = {"type": "object", "properties": {"items": {"type": "array"}}}
        errors = _validate_json_against_schema({"items": "abc"}, schema)
        assert any("array" in e for e in errors)


# ── Complete() Branches ────────────────────────────────────────────────


class TestCompleteBranches:
    """Cover complete() HTTP error and metering branches."""

    @pytest.mark.asyncio
    async def test_http_status_error_raises(self):
        client = LLMClient(api_key="sk-test")
        with patch("httpx.AsyncClient") as MockClient:
            mock_http = MagicMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)

            # Mock the response to raise HTTPStatusError on raise_for_status
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "unauthorized", request=MagicMock(), response=mock_response
            )
            mock_http.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_http

            with pytest.raises(RuntimeError, match="LLM API error"):
                await client.complete(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "hi"}],
                )

    @pytest.mark.asyncio
    async def test_request_error_raises(self):
        client = LLMClient(api_key="sk-test")
        with patch("httpx.AsyncClient") as MockClient:
            mock_http = MagicMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(side_effect=httpx.RequestError("network down"))
            MockClient.return_value = mock_http

            with pytest.raises(RuntimeError, match="LLM API request failed"):
                await client.complete(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "hi"}],
                )

    @pytest.mark.asyncio
    async def test_token_economics_failure_is_logged(self):
        """When token_economics.record_call raises, complete() still returns response."""
        class _FailingTE:
            async def record_call(self, **kwargs):
                raise RuntimeError("te down")

        te = _FailingTE()
        # review_run_id must be set to enter the metering branch
        client = LLMClient(
            api_key="sk-test",
            token_economics=te,
            review_run_id="00000000-0000-0000-0000-000000000001",
        )
        with patch("httpx.AsyncClient") as MockClient:
            mock_http = MagicMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)

            mock_response = MagicMock()
            mock_response.json.return_value = _mock_response("hello")
            mock_response.raise_for_status = MagicMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_http

            resp = await client.complete(
                model="gpt-4o",
                messages=[{"role": "user", "content": "hi"}],
            )
        assert resp.content == "hello"


# ── Structured Response Branches ──────────────────────────────────────


class TestStructuredResponseBranches:
    """Cover branches in complete_structured() — retries, validation."""

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        """First response fails validation, second succeeds."""
        client = LLMClient(api_key="sk-test")
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        bad_resp = MagicMock()
        bad_resp.json.return_value = _mock_response('{"name": 123}')  # wrong type
        bad_resp.raise_for_status = MagicMock()

        good_resp = MagicMock()
        good_resp.json.return_value = _mock_response('{"name": "ok"}')
        good_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_http = MagicMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(side_effect=[bad_resp, good_resp])
            MockClient.return_value = mock_http

            result = await client.complete_structured(
                model="gpt-4o",
                messages=[{"role": "user", "content": "hi"}],
                schema=schema,
                max_retries=2,
            )
        assert result == {"name": "ok"}

    @pytest.mark.asyncio
    async def test_all_retries_fail(self):
        """All retries fail → RuntimeError raised."""
        client = LLMClient(api_key="sk-test")
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}

        bad_resp = MagicMock()
        bad_resp.json.return_value = _mock_response('{"name": 123}')
        bad_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_http = MagicMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(return_value=bad_resp)
            MockClient.return_value = mock_http

            with pytest.raises(ValueError, match="valid structured response"):
                await client.complete_structured(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "hi"}],
                    schema=schema,
                    max_retries=1,
                )
