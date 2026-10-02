"""
Tests for GitHub API client — App auth, PR comment posting, review posting.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from verdity.github_client import (
    GitHubClient,
    GitHubClientError,
    create_annotations,
    create_check_output,
)
from verdity.schemas import ConcernType, Finding, Severity

# ── Fixtures ──────────────────────────────────────────────────────────


def _generate_test_private_key() -> str:
    """Generate a real RSA private key for tests."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


SAMPLE_PRIVATE_KEY = _generate_test_private_key()


def _make_client(**kwargs) -> GitHubClient:
    """Create a GitHubClient with sensible test defaults."""
    defaults = {
        "app_id": 12345,
        "private_key_pem": SAMPLE_PRIVATE_KEY,
        "installation_id": "67890",
    }
    defaults.update(kwargs)
    return GitHubClient(**defaults)


# ── JWT Generation ────────────────────────────────────────────────────


class TestJWTGeneration:
    def test_generates_jwt(self):
        client = _make_client()
        token = client._generate_jwt()
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 50  # JWT tokens are long

    def test_jwt_cached_within_lifetime(self):
        client = _make_client(token_lifetime_seconds=600)
        t1 = client._generate_jwt()
        t2 = client._generate_jwt()
        assert t1 == t2  # same token returned (cached)

    def test_jwt_refreshed_after_expiry(self):
        client = _make_client(token_lifetime_seconds=10)
        with patch("verdity.github_client.time") as mock_time:
            mock_time.time.return_value = 1000.0
            t1 = client._generate_jwt()
            mock_time.time.return_value = 1020.0  # 20s later > lifetime
            t2 = client._generate_jwt()
        assert t1 != t2

    @patch("verdity.github_client.jwt.encode")
    def test_jwt_payload(self, mock_encode):
        mock_encode.return_value = "mock.jwt.token"
        client = _make_client(app_id=99999)
        token = client._generate_jwt()
        assert token == "mock.jwt.token"
        mock_encode.assert_called_once()
        payload = mock_encode.call_args[0][0]
        assert payload["iss"] == "99999"
        assert "iat" in payload
        assert "exp" in payload
        assert payload["exp"] > payload["iat"]


# ── Installation Token ────────────────────────────────────────────────


class TestInstallationToken:
    @pytest.mark.asyncio
    async def test_get_installation_token_success(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "token": "ghs_test_token_abc123",
            "expires_at": "2099-01-01T00:00:00Z",
        }

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_response
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            token = await client._get_installation_token(mock_http)
            assert token == "ghs_test_token_abc123"

    @pytest.mark.asyncio
    async def test_get_installation_token_caches(self):
        client = _make_client()
        client._installationToken = "cached_token"
        client._token_expires_at = time.time() + 3600

        mock_http = AsyncMock()
        token = await client._get_installation_token(mock_http)
        assert token == "cached_token"
        mock_http.get.assert_not_called()  # no API call made

    @pytest.mark.asyncio
    async def test_get_installation_token_empty_expires(self):
        """Token response without expires_at falls back to now + 3600."""
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"token": "ghs_abc"}

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_resp
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            token = await client._get_installation_token(mock_http)
            assert token == "ghs_abc"
            assert client._token_expires_at > time.time()
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response

        with pytest.raises(GitHubClientError, match="Failed to get installation token"):
            await client._get_installation_token(mock_http)


# ── Post PR Comment ──────────────────────────────────────────────────


class TestPostPRComment:
    @pytest.mark.asyncio
    async def test_post_comment_success(self):
        client = _make_client()
        expected = {"id": 1, "body": "Hello from Verdity"}

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = expected

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            result = await client.post_pr_comment("org", "repo", 42, "Review complete")
            assert result == expected

    @pytest.mark.asyncio
    async def test_post_comment_failure(self):
        client = _make_client()

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            with pytest.raises(GitHubClientError, match="Failed to post PR comment"):
                await client.post_pr_comment("org", "repo", 99, "Should fail")


# ── Post PR Review ────────────────────────────────────────────────────


class TestPostPRReview:
    @pytest.mark.asyncio
    async def test_post_review_success(self):
        client = _make_client()
        expected = {"id": 100, "state": "COMMENTED"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            result = await client.post_pr_review(
                "org",
                "repo",
                42,
                "LGTM",
                event="APPROVE",
                commit_id="abc123",
            )
            assert result == expected

    @pytest.mark.asyncio
    async def test_post_review_with_inline_comments(self):
        """Review with inline comments posts correct payload."""
        client = _make_client()
        comments = [{"path": "main.py", "line": 10, "body": "Fix this"}]

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 200}

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 201
        mock_token_resp.json.return_value = {
            "token": "ghs_inst123",
            "expires_at": "2099-01-01T00:00:00Z",
        }

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_token_resp
            mock_http.post.return_value = mock_response
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            result = await client.post_pr_review(
                "org", "repo", 42, body="Review", event="COMMENT", comments=comments
            )
            assert result == {"id": 200}
            call_kwargs = mock_http.post.call_args
            assert call_kwargs[1]["json"]["comments"] == comments

    @pytest.mark.asyncio
    async def test_post_review_failure(self):
        client = _make_client()

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = "Validation Failed"

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            with pytest.raises(GitHubClientError, match="Failed to post PR review"):
                await client.post_pr_review("org", "repo", 42, "test")


# ── Post Inline Comment ──────────────────────────────────────────────


class TestPostInlineComment:
    @pytest.mark.asyncio
    async def test_inline_comment_success(self):
        client = _make_client()
        expected = {"id": 200, "path": "src/main.py"}

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = expected

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            result = await client.post_inline_comment(
                "org",
                "repo",
                42,
                "Security issue here",
                commit_id="abc123",
                path="src/main.py",
                line=10,
            )
            assert result == expected

    @pytest.mark.asyncio
    async def test_inline_comment_failure(self):
        client = _make_client()

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            with pytest.raises(GitHubClientError, match="Failed to post inline comment"):
                await client.post_inline_comment(
                    "org",
                    "repo",
                    42,
                    "test",
                    commit_id="abc123",
                    path="src/main.py",
                    line=10,
                )


# ── Get PR ────────────────────────────────────────────────────────────


class TestGetPR:
    @pytest.mark.asyncio
    async def test_get_pr_success(self):
        client = _make_client()
        expected = {"number": 42, "title": "Fix bug", "state": "open"}

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 201
        mock_token_resp.json.return_value = {
            "token": "ghs_inst123",
            "expires_at": "2099-01-01T00:00:00Z",
        }

        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = expected

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=[mock_token_resp, mock_pr_resp])
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            result = await client.get_pr("org", "repo", 42)
            assert result == expected

    @pytest.mark.asyncio
    async def test_get_pr_failure(self):
        client = _make_client()

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 201
        mock_token_resp.json.return_value = {
            "token": "ghs_inst123",
            "expires_at": "2099-01-01T00:00:00Z",
        }

        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 404
        mock_pr_resp.text = "Not Found"

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=[mock_token_resp, mock_pr_resp])
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            with pytest.raises(GitHubClientError, match="Failed to get PR"):
                await client.get_pr("org", "repo", 99)


# ── Close ─────────────────────────────────────────────────────────────


class TestClose:
    def test_close_clears_tokens(self):
        client = _make_client()
        client._jwt = "some.jwt.token"
        client._installationToken = "ghs_some_token"
        import asyncio

        asyncio.run(client.close())
        assert client._jwt is None
        assert client._installationToken is None


# ── Error class ───────────────────────────────────────────────────────


class TestGitHubClientError:
    def test_is_exception(self):
        assert issubclass(GitHubClientError, Exception)
        err = GitHubClientError("test error")
        assert str(err) == "test error"


# ── GitHub Checks API: Annotations ──────────────────────────────────────


class TestCreateAnnotations:
    """Test create_annotations helper function."""

    def _make_finding(self, **kwargs) -> Finding:
        """Create a test finding."""
        defaults = {
            "concern": ConcernType.SECURITY,
            "severity": Severity.HIGH,
            "file": "src/test.py",
            "line_start": 10,
            "line_end": 10,
            "summary": "Test finding",
            "explanation": "Test explanation",
            "confidence": 0.8,
            "agent_version": "test@0.1.0",
            "prompt_hash": "sha256:abc123",
        }
        defaults.update(kwargs)
        return Finding(**defaults)

    def test_create_annotations_basic(self):
        """Should convert findings to GitHub annotations format."""
        findings = [
            self._make_finding(
                severity=Severity.CRITICAL,
                file="src/auth.py",
                line_start=15,
                line_end=20,
                summary="SQL injection vulnerability",
            ),
            self._make_finding(
                severity=Severity.MEDIUM,
                file="src/utils.py",
                line_start=5,
                line_end=5,
                summary="Unused variable",
            ),
        ]

        annotations = create_annotations(findings)

        assert len(annotations) == 2
        # CRITICAL severity -> failure
        assert annotations[0]["annotation_level"] == "failure"
        assert annotations[0]["path"] == "src/auth.py"
        assert annotations[0]["start_line"] == 15
        assert annotations[0]["end_line"] == 20
        assert annotations[0]["message"] == "SQL injection vulnerability"
        assert annotations[0]["title"] == "security"
        # MEDIUM severity -> warning
        assert annotations[1]["annotation_level"] == "warning"
        assert annotations[1]["path"] == "src/utils.py"

    def test_create_annotations_severity_mapping(self):
        """Should map severity to correct annotation_level."""
        severity_mapping = [
            (Severity.CRITICAL, "failure"),
            (Severity.HIGH, "failure"),
            (Severity.MEDIUM, "warning"),
            (Severity.LOW, "warning"),
            (Severity.INFO, "warning"),
        ]

        for severity, expected_level in severity_mapping:
            finding = self._make_finding(severity=severity)
            annotations = create_annotations([finding])
            assert annotations[0]["annotation_level"] == expected_level, f"Failed for {severity}"

    def test_create_annotations_with_suggested_fix(self):
        """Should include auto-fix suggestions as notice annotations."""
        finding = self._make_finding(
            severity=Severity.HIGH,
            file="src/auth.py",
            line_start=10,
            line_end=15,
            summary="Hardcoded secret",
            suggested_fix_diff="- password = 'secret123'\n+ password = os.getenv('PASSWORD')",
        )

        annotations = create_annotations([finding])

        # Should have 2 annotations: one for the finding, one for the fix
        assert len(annotations) == 2
        # First annotation: the finding itself
        assert annotations[0]["annotation_level"] == "failure"
        assert annotations[0]["message"] == "Hardcoded secret"
        # Second annotation: the suggested fix
        assert annotations[1]["annotation_level"] == "notice"
        assert "Suggested fix:" in annotations[1]["message"]
        assert "password = os.getenv('PASSWORD')" in annotations[1]["message"]
        assert annotations[1]["title"] == "Auto-fix available"

    def test_create_annotations_without_suggested_fix(self):
        """Should not add fix annotation when no suggested fix."""
        finding = self._make_finding(
            severity=Severity.HIGH,
            suggested_fix_diff=None,
        )

        annotations = create_annotations([finding])

        assert len(annotations) == 1
        assert annotations[0]["annotation_level"] == "failure"

    def test_create_annotations_empty_list(self):
        """Should return empty list for empty findings."""
        annotations = create_annotations([])
        assert annotations == []

    def test_create_annotations_limits_to_50(self):
        """Should limit annotations to GitHub's 50 max."""
        findings = [
            self._make_finding(file=f"src/file{i}.py", line_start=i + 1, line_end=i + 1)
            for i in range(60)
        ]

        annotations = create_annotations(findings)

        assert len(annotations) == 50


# ── GitHub Checks API: Check Output ─────────────────────────────────────


class TestCreateCheckOutput:
    """Test create_check_output helper function."""

    def _make_finding(self, **kwargs) -> Finding:
        """Create a test finding."""
        defaults = {
            "concern": ConcernType.SECURITY,
            "severity": Severity.HIGH,
            "file": "src/test.py",
            "line_start": 10,
            "line_end": 10,
            "summary": "Test finding",
            "explanation": "Test explanation",
            "confidence": 0.8,
            "agent_version": "test@0.1.0",
            "prompt_hash": "sha256:abc123",
        }
        defaults.update(kwargs)
        return Finding(**defaults)

    def test_create_check_output_basic(self):
        """Should create output with title, summary, text, and annotations."""
        findings = [
            self._make_finding(
                severity=Severity.CRITICAL,
                file="src/auth.py",
                line_start=10,
                line_end=15,
                summary="SQL injection",
            ),
            self._make_finding(
                severity=Severity.MEDIUM,
                file="src/utils.py",
                line_start=5,
                line_end=5,
                summary="Unused import",
            ),
        ]

        output = create_check_output(findings)

        assert output["title"] == "Verdity Code Review"
        assert "**2 issues**" in output["summary"]
        assert "src/auth.py" in output["text"]
        assert "src/utils.py" in output["text"]
        assert "SQL injection" in output["text"]
        assert "Unused import" in output["text"]
        assert "annotations" in output
        assert len(output["annotations"]) == 2

    def test_create_check_output_empty_findings(self):
        """Should create success output when no findings."""
        output = create_check_output([])

        assert output["title"] == "Verdity Code Review"
        assert "0 issues" in output["summary"]
        assert "No issues found" in output["text"]
        assert output["annotations"] == []

    def test_create_check_output_includes_all_concerns(self):
        """Should group findings by concern type in markdown."""
        findings = [
            self._make_finding(concern=ConcernType.SECURITY, severity=Severity.CRITICAL),
            self._make_finding(concern=ConcernType.CODE_QUALITY, severity=Severity.HIGH),
            self._make_finding(concern=ConcernType.TESTING, severity=Severity.MEDIUM),
            self._make_finding(concern=ConcernType.DOCUMENTATION, severity=Severity.LOW),
        ]

        output = create_check_output(findings)

        assert "security" in output["text"].lower()
        assert "code_quality" in output["text"].lower()
        assert "testing" in output["text"].lower()
        assert "documentation" in output["text"].lower()

    def test_create_check_output_annotations_limited(self):
        """Should limit annotations to 50 in output."""
        findings = [
            self._make_finding(file=f"src/file{i}.py", line_start=i + 1, line_end=i + 1)
            for i in range(60)
        ]

        output = create_check_output(findings)

        assert len(output["annotations"]) == 50


# ── GitHub Checks API: Action Buttons ───────────────────────────────────


class TestCheckRunActions:
    """Test action buttons support in check runs."""

    @pytest.mark.asyncio
    async def test_create_check_run_with_actions(self):
        """create_check_run should accept and send actions parameter."""
        client = _make_client()

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(
                200, json={"id": 12345, "status": "in_progress"}
            )

            actions = [
                {"label": "Re-run Verdity", "description": "Re-run review", "identifier": "rerun-verdity"},
                {"label": "Dismiss Findings", "description": "Dismiss all", "identifier": "dismiss-findings"},
            ]

            result = await client.create_check_run(
                owner="test-owner",
                repo="test-repo",
                name="verdity-review",
                head_sha="abc123",
                status="in_progress",
                actions=actions,
            )

            assert result["id"] == 12345
            # Verify actions were sent in request
            call_kwargs = mock_request.call_args
            assert "actions" in call_kwargs[1]["json"]
            assert call_kwargs[1]["json"]["actions"] == actions

    @pytest.mark.asyncio
    async def test_update_check_run_with_actions(self):
        """update_check_run should accept and send actions parameter."""
        client = _make_client()

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(
                200, json={"id": 12345, "status": "completed", "conclusion": "success"}
            )

            actions = [
                {"label": "Re-run Verdity", "description": "Re-run review", "identifier": "rerun-verdity"},
            ]

            result = await client.update_check_run(
                owner="test-owner",
                repo="test-repo",
                check_run_id=12345,
                status="completed",
                conclusion="success",
                actions=actions,
            )

            assert result["conclusion"] == "success"
            call_kwargs = mock_request.call_args
            assert "actions" in call_kwargs[1]["json"]
            assert call_kwargs[1]["json"]["actions"] == actions


# ── GitHub Checks API: Integration Tests ────────────────────────────────


class TestCheckRunIntegration:
    """Integration tests for enhanced check runs."""

    def _make_finding(self, **kwargs) -> Finding:
        """Create a test finding."""
        defaults = {
            "concern": ConcernType.SECURITY,
            "severity": Severity.HIGH,
            "file": "src/test.py",
            "line_start": 10,
            "line_end": 10,
            "summary": "Test finding",
            "explanation": "Test explanation",
            "confidence": 0.8,
            "agent_version": "test@0.1.0",
            "prompt_hash": "sha256:abc123",
        }
        defaults.update(kwargs)
        return Finding(**defaults)

    @pytest.mark.asyncio
    async def test_create_check_run_with_output(self):
        """create_check_run should accept output parameter with annotations."""
        client = _make_client()

        findings = [
            self._make_finding(
                severity=Severity.CRITICAL,
                file="src/auth.py",
                line_start=10,
                line_end=15,
                summary="SQL injection",
            ),
        ]

        output = create_check_output(findings)

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(
                200, json={"id": 12345, "status": "in_progress"}
            )

            result = await client.create_check_run(
                owner="test-owner",
                repo="test-repo",
                name="verdity-review",
                head_sha="abc123",
                status="in_progress",
                output=output,
            )

            assert result["id"] == 12345
            call_kwargs = mock_request.call_args
            assert "output" in call_kwargs[1]["json"]
            assert call_kwargs[1]["json"]["output"]["title"] == "Verdity Code Review"
            assert "annotations" in call_kwargs[1]["json"]["output"]

    @pytest.mark.asyncio
    async def test_update_check_run_with_output(self):
        """update_check_run should accept output parameter with annotations."""
        client = _make_client()

        findings = [
            self._make_finding(
                severity=Severity.HIGH,
                file="src/auth.py",
                line_start=10,
                line_end=15,
                summary="Hardcoded secret",
            ),
        ]

        output = create_check_output(findings)

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(
                200, json={"id": 12345, "status": "completed", "conclusion": "failure"}
            )

            result = await client.update_check_run(
                owner="test-owner",
                repo="test-repo",
                check_run_id=12345,
                status="completed",
                conclusion="failure",
                output=output,
            )

            assert result["conclusion"] == "failure"
            call_kwargs = mock_request.call_args
            assert "output" in call_kwargs[1]["json"]
            assert call_kwargs[1]["json"]["output"]["annotations"] != []

    @pytest.mark.asyncio
    async def test_full_check_run_lifecycle(self):
        """Test complete check run: create in_progress, update with findings."""
        client = _make_client()

        findings = [
            self._make_finding(
                severity=Severity.CRITICAL,
                file="src/auth.py",
                line_start=10,
                line_end=15,
                summary="SQL injection",
                suggested_fix_diff="- execute(query)\n+ execute(query, params)",
            ),
            self._make_finding(
                severity=Severity.MEDIUM,
                file="src/utils.py",
                line_start=5,
                line_end=5,
                summary="Unused variable",
            ),
        ]

        output = create_check_output(findings)
        actions = [
            {"label": "Re-run Verdity", "description": "Re-run review", "identifier": "rerun-verdity"},
            {"label": "Dismiss Findings", "description": "Dismiss all", "identifier": "dismiss-findings"},
        ]

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            # First call: create check run
            mock_request.return_value = httpx.Response(
                200, json={"id": 12345, "status": "in_progress"}
            )

            await client.create_check_run(
                owner="test-owner",
                repo="test-repo",
                name="verdity-review",
                head_sha="abc123",
                status="in_progress",
            )

            # Second call: update with findings
            mock_request.return_value = httpx.Response(
                200, json={"id": 12345, "status": "completed", "conclusion": "failure"}
            )

            result = await client.update_check_run(
                owner="test-owner",
                repo="test-repo",
                check_run_id=12345,
                status="completed",
                conclusion="failure",
                output=output,
                actions=actions,
            )

            assert result["conclusion"] == "failure"
            assert mock_request.call_count == 2


# ── GitHub Checks API: Additional Parameter Tests ───────────────────────


class TestCheckRunAdditionalParams:
    """Test create_check_run and update_check_run with all parameters."""

    @pytest.mark.asyncio
    async def test_create_check_run_with_all_params(self):
        """create_check_run should accept all optional parameters."""
        client = _make_client()

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(
                200, json={"id": 12345, "status": "completed", "conclusion": "success"}
            )

            result = await client.create_check_run(
                owner="test-owner",
                repo="test-repo",
                name="verdity-review",
                head_sha="abc123",
                status="completed",
                conclusion="success",
                output={"title": "Test", "summary": "Done"},
                started_at="2024-01-01T00:00:00Z",
                completed_at="2024-01-01T00:01:00Z",
                actions=[{"label": "Action", "description": "Desc", "identifier": "action"}],
            )

            assert result["conclusion"] == "success"
            call_kwargs = mock_request.call_args
            payload = call_kwargs[1]["json"]
            assert payload["conclusion"] == "success"
            assert payload["started_at"] == "2024-01-01T00:00:00Z"
            assert payload["completed_at"] == "2024-01-01T00:01:00Z"
            assert "actions" in payload

    @pytest.mark.asyncio
    async def test_create_check_run_error_handling(self):
        """create_check_run should raise GitHubClientError on failure."""
        client = _make_client()

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(400, text="Bad Request")

            with pytest.raises(GitHubClientError, match="Failed to create check run"):
                await client.create_check_run(
                    owner="test-owner",
                    repo="test-repo",
                    name="verdity-review",
                    head_sha="abc123",
                )

    @pytest.mark.asyncio
    async def test_update_check_run_with_all_params(self):
        """update_check_run should accept all optional parameters."""
        client = _make_client()

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(
                200, json={"id": 12345, "status": "completed", "conclusion": "failure"}
            )

            result = await client.update_check_run(
                owner="test-owner",
                repo="test-repo",
                check_run_id=12345,
                status="completed",
                conclusion="failure",
                output={"title": "Test", "summary": "Failed"},
                completed_at="2024-01-01T00:01:00Z",
                actions=[{"label": "Retry", "description": "Retry", "identifier": "retry"}],
            )

            assert result["conclusion"] == "failure"
            call_kwargs = mock_request.call_args
            payload = call_kwargs[1]["json"]
            assert payload["conclusion"] == "failure"
            assert payload["completed_at"] == "2024-01-01T00:01:00Z"
            assert "actions" in payload

    @pytest.mark.asyncio
    async def test_update_check_run_error_handling(self):
        """update_check_run should raise GitHubClientError on failure."""
        client = _make_client()

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(404, text="Not Found")

            with pytest.raises(GitHubClientError, match="Failed to update check run"):
                await client.update_check_run(
                    owner="test-owner",
                    repo="test-repo",
                    check_run_id=12345,
                )


# ── Client Management Tests ─────────────────────────────────────────────


class TestClientManagement:
    """Test client lifecycle and internal methods."""

    @pytest.mark.asyncio
    async def test_request_method(self):
        """_request should make authenticated HTTP request."""
        client = _make_client()

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(200, json={"data": "test"})

            # Call the internal _request method
            result = await client._request("GET", "https://api.github.com/test")

            assert result.json() == {"data": "test"}
            mock_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_clears_client(self):
        """close should clear tokens and close HTTP client."""
        client = _make_client()
        client._jwt = "some.jwt.token"
        client._installationToken = "ghs_some_token"
        # Mock the client
        mock_http_client = AsyncMock()
        mock_http_client.is_closed = False
        client._client = mock_http_client

        await client.close()

        assert client._jwt is None
        assert client._installationToken is None
        mock_http_client.aclose.assert_called_once()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """Async context manager should work correctly."""
        client = _make_client()

        with (
            patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(200, json={"id": 1})

            async with client as c:
                assert c is client
                result = await c.create_check_run(
                    owner="test-owner",
                    repo="test-repo",
                    name="verdity-review",
                    head_sha="abc123",
                )
                assert result["id"] == 1

            # After exit, client should be closed
            assert client._client is None
