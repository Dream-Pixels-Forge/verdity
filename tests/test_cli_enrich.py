"""
Tests for verdity-enrich CLI commands (SG-02, SG-03).

Covers:
- enrich command - LLM enrichment of findings
- stdin handling
- --output-file option
- Error handling for invalid input
- UUID handling for finding_id
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdity.cli.enrich import main, enrich_findings_command


class TestEnrichCLI:
    """Test verdity-enrich CLI commands via argparse."""

    def _make_findings_json(self) -> str:
        """Create a sample findings JSON content."""
        return json.dumps(
            [
                {
                    "finding_id": "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                    "concern": "security",
                    "severity": "high",
                    "file": "auth/login.py",
                    "line_start": 15,
                    "line_end": 15,
                    "summary": "Potential SQL injection via f-string",
                    "explanation": "User input directly interpolated into SQL query",
                    "confidence": 0.85,
                    "agent_version": "security-agent@0.1.0",
                    "prompt_hash": "sha256:abc123",
                },
                {
                    "finding_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "concern": "code_quality",
                    "severity": "medium",
                    "file": "utils/helpers.py",
                    "line_start": 42,
                    "line_end": 42,
                    "summary": "Function too complex",
                    "explanation": "Function has too many branches",
                    "confidence": 0.75,
                    "agent_version": "quality-agent@0.1.0",
                    "prompt_hash": "sha256:def456",
                },
            ]
        )

    def _make_diff_content(self) -> str:
        """Create sample diff content."""
        return """diff --git a/src/auth.py b/src/auth.py
index abc123..def456 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,5 +1,5 @@
 def login(user_id, password):
-    query = f"SELECT * FROM users WHERE id = {user_id}"
+    query = "SELECT * FROM users WHERE id = ?"
     cursor.execute(query, (user_id,))
     return cursor.fetchone()
"""

    def _run_main(self, args: list[str]) -> tuple[int, str, str]:
        """Run main() with given args, return (exit_code, stdout, stderr)."""
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.argv = ["verdity-enrich"] + args
        from io import StringIO

        stdout_capture = StringIO()
        stderr_capture = StringIO()
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture
        try:
            exit_code = main()
        except SystemExit as e:
            exit_code = e.code
        finally:
            stdout = stdout_capture.getvalue()
            stderr = stderr_capture.getvalue()
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        return exit_code, stdout, stderr

    def test_enrich_main_help(self):
        """enrich command should show help."""
        exit_code, stdout, stderr = self._run_main(["--help"])
        assert exit_code == 0
        assert "enrich" in stdout.lower() or "run-id" in stdout

    def test_enrich_run_id_required(self):
        """enrich command should require --run-id."""
        exit_code, stdout, stderr = self._run_main([])
        assert exit_code != 0
        assert "required" in stderr.lower() or "error" in stderr.lower()

    def test_enrich_invalid_run_id(self):
        """enrich command should reject invalid --run-id."""
        exit_code, stdout, stderr = self._run_main(
            ["--run-id", "not-a-uuid", "--mode", "explain"]
        )
        assert exit_code == 1
        assert "Invalid UUID" in stderr

    def test_enrich_invalid_mode(self):
        """enrich command should reject invalid --mode."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(self._make_findings_json())
            findings_file = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(self._make_diff_content())
            diff_file = f.name

        try:
            exit_code, stdout, stderr = self._run_main(
                [
                    "--run-id",
                    "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                    "--mode", "invalid",
                    "--findings-file",
                    findings_file,
                    "--diff-file",
                    diff_file,
                ]
            )
            assert exit_code == 2  # argparse returns 2 for invalid arguments
            assert "Invalid mode" in stderr or "invalid choice" in stderr.lower()
        finally:
            Path(findings_file).unlink()
            Path(diff_file).unlink()

    def test_enrich_empty_diff(self):
        """enrich command should reject empty diff."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(self._make_findings_json())
            findings_file = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("")  # Empty diff
            diff_file = f.name

        try:
            exit_code, stdout, stderr = self._run_main(
                [
                    "--run-id",
                    "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                    "--mode",
                    "explain",
                    "--findings-file",
                    findings_file,
                    "--diff-file",
                    diff_file,
                ]
            )
            assert exit_code == 1
            assert "diff is required" in stderr.lower()
        finally:
            Path(findings_file).unlink()
            Path(diff_file).unlink()


class TestEnrichModeOptions:
    """Test enrich mode options."""

    def _run_main(self, args: list[str]) -> tuple[int, str, str]:
        """Run main() with given args, return (exit_code, stdout, stderr)."""
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.argv = ["verdity-enrich"] + args
        from io import StringIO

        stdout_capture = StringIO()
        stderr_capture = StringIO()
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture
        try:
            exit_code = main()
        except SystemExit as e:
            exit_code = e.code
        finally:
            stdout = stdout_capture.getvalue()
            stderr = stderr_capture.getvalue()
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        return exit_code, stdout, stderr

    def test_enrich_modes_available(self):
        """enrich command help should list all valid modes."""
        exit_code, stdout, stderr = self._run_main(["--help"])
        assert exit_code == 0
        assert "explain" in stdout
        assert "fix" in stdout
        assert "risk" in stdout
        assert "all" in stdout


class TestEnrichInputValidation:
    """Test enrich input validation."""

    def _make_diff_content(self) -> str:
        """Create sample diff content."""
        return "diff content"

    def _run_main(self, args: list[str]) -> tuple[int, str, str]:
        """Run main() with given args, return (exit_code, stdout, stderr)."""
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.argv = ["verdity-enrich"] + args
        from io import StringIO

        stdout_capture = StringIO()
        stderr_capture = StringIO()
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture
        try:
            exit_code = main()
        except SystemExit as e:
            exit_code = e.code
        finally:
            stdout = stdout_capture.getvalue()
            stderr = stderr_capture.getvalue()
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        return exit_code, stdout, stderr

    def test_enrich_malformed_json(self):
        """enrich command should handle malformed JSON."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json content")
            findings_file = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(self._make_diff_content())
            diff_file = f.name

        try:
            exit_code, stdout, stderr = self._run_main(
                [
                    "--run-id",
                    "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                    "--mode",
                    "explain",
                    "--findings-file",
                    findings_file,
                    "--diff-file",
                    diff_file,
                ]
            )
            # Should handle gracefully, not crash with unhandled exception
            assert exit_code != 0
            assert "JSON" in stderr or "error" in stderr.lower()
        finally:
            Path(findings_file).unlink()
            Path(diff_file).unlink()

    def test_enrich_missing_concern(self):
        """enrich command should handle missing concern field."""
        findings_no_concern = json.dumps(
            [
                {
                    "finding_id": "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                    # No concern field
                    "severity": "high",
                    "file": "test.py",
                    "summary": "Test",
                    "explanation": "Test",
                    "confidence": 0.5,
                }
            ]
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(findings_no_concern)
            findings_file = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(self._make_diff_content())
            diff_file = f.name

        try:
            exit_code, stdout, stderr = self._run_main(
                [
                    "--run-id",
                    "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                    "--mode",
                    "explain",
                    "--findings-file",
                    findings_file,
                    "--diff-file",
                    diff_file,
                ]
            )
            # Should handle gracefully, not crash with KeyError
            assert "KeyError" not in stderr
            assert "Traceback" not in stderr
        finally:
            Path(findings_file).unlink()
            Path(diff_file).unlink()


class TestEnrichFindingsParsing:
    """Test findings parsing edge cases."""

    def _make_diff_content(self) -> str:
        """Create sample diff content."""
        return "diff content"

    def _run_main(self, args: list[str]) -> tuple[int, str, str]:
        """Run main() with given args, return (exit_code, stdout, stderr)."""
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.argv = ["verdity-enrich"] + args
        from io import StringIO

        stdout_capture = StringIO()
        stderr_capture = StringIO()
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture
        try:
            exit_code = main()
        except SystemExit as e:
            exit_code = e.code
        finally:
            stdout = stdout_capture.getvalue()
            stderr = stderr_capture.getvalue()
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        return exit_code, stdout, stderr

    def test_enrich_findings_without_id_generates_new_uuid(self):
        """enrich command should generate UUID when finding_id is missing."""
        findings_no_id = json.dumps(
            [
                {
                    # No finding_id provided - should generate a new UUID
                    "concern": "security",
                    "severity": "high",
                    "file": "test.py",
                    "line_start": 10,
                    "line_end": 10,
                    "summary": "Test finding without ID",
                    "explanation": "Test",
                    "confidence": 0.5,
                }
            ]
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(findings_no_id)
            findings_file = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(self._make_diff_content())
            diff_file = f.name

        try:
            exit_code, stdout, stderr = self._run_main(
                [
                    "--run-id",
                    "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                    "--mode",
                    "explain",
                    "--findings-file",
                    findings_file,
                    "--diff-file",
                    diff_file,
                ]
            )
            # Should not crash with KeyError for finding_id
            # The result.exit_code might be non-zero due to LLM gateway, but should not crash
            assert "KeyError" not in stderr
            assert "Traceback" not in stderr
        finally:
            Path(findings_file).unlink()
            Path(diff_file).unlink()


class TestEnrichArguments:
    """Test enrich command arguments."""

    def _run_main(self, args: list[str]) -> tuple[int, str, str]:
        """Run main() with given args, return (exit_code, stdout, stderr)."""
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.argv = ["verdity-enrich"] + args
        from io import StringIO

        stdout_capture = StringIO()
        stderr_capture = StringIO()
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture
        try:
            exit_code = main()
        except SystemExit as e:
            exit_code = e.code
        finally:
            stdout = stdout_capture.getvalue()
            stderr = stderr_capture.getvalue()
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        return exit_code, stdout, stderr

    def test_enrich_model_argument(self):
        """enrich command should accept --model argument."""
        exit_code, stdout, stderr = self._run_main(["--help"])
        assert exit_code == 0
        assert "--model" in stdout

    def test_enrich_backend_argument(self):
        """enrich command should accept --backend argument."""
        exit_code, stdout, stderr = self._run_main(["--help"])
        assert exit_code == 0
        assert "--backend" in stdout

    def test_enrich_findings_file_argument(self):
        """enrich command should accept --findings-file argument."""
        exit_code, stdout, stderr = self._run_main(["--help"])
        assert exit_code == 0
        assert "--findings-file" in stdout

    def test_enrich_diff_file_argument(self):
        """enrich command should accept --diff-file argument."""
        exit_code, stdout, stderr = self._run_main(["--help"])
        assert exit_code == 0
        assert "--diff-file" in stdout

    def test_enrich_output_file_argument(self):
        """enrich command should accept --output-file argument."""
        exit_code, stdout, stderr = self._run_main(["--help"])
        assert exit_code == 0
        assert "--output-file" in stdout


class TestEnrichFindingsCommand:
    """Test the enrich_findings_command function directly with mocked dependencies."""

    @pytest.fixture
    def mock_args(self):
        """Create a mock argparse.Namespace with valid arguments."""
        args = argparse.Namespace()
        args.run_id = "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f"
        args.mode = "explain"
        args.model = "qwen2.5-7b-instruct"
        args.backend = "ollama"
        args.base_url = None
        args.temperature = 0.0
        args.max_tokens = 4096
        args.findings_file = None
        args.diff_file = None
        args.repo_owner = "test-owner"
        args.repo_name = "test-repo"
        args.db_path = ":memory:"
        args.output_file = None
        return args

    @pytest.mark.asyncio
    async def test_enrich_findings_command_success(self, mock_args):
        """Test enrich_findings_command with mocked LLM gateway."""
        findings_data = [
            {
                "finding_id": "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                "concern": "security",
                "severity": "high",
                "file": "auth/login.py",
                "line_start": 15,
                "line_end": 15,
                "summary": "Potential SQL injection",
                "explanation": "User input directly interpolated",
                "confidence": 0.85,
                "agent_version": "security-agent@0.1.0",
                "prompt_hash": "sha256:abc123",
            }
        ]
        diff = "diff content"

        with patch("verdity.cli.enrich.LocalLLMGateway") as mock_gateway_class:
            mock_gateway = AsyncMock()
            mock_gateway_class.return_value = mock_gateway

            with patch("verdity.cli.enrich.LLMEnricherAgent") as mock_enricher_class:
                mock_enricher = AsyncMock()
                mock_enricher.enrich_findings = AsyncMock(
                    return_value=[
                        MagicMock(
                            finding_id="d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                            mode=MagicMock(value="explain"),
                            model="qwen2.5-7b-instruct",
                            backend="ollama",
                            explanation="LLM explanation",
                            confidence_adjustment_rationale="Rationale",
                            fix_diff=None,
                            fix_explanation=None,
                            risk_score=None,
                            cwe_ids=None,
                            exploitability=None,
                            blast_radius=None,
                        )
                    ]
                )
                mock_enricher_class.return_value = mock_enricher

                with patch("verdity.cli.enrich.AuditStore") as mock_audit_class:
                    mock_audit = AsyncMock()
                    mock_audit.connect = AsyncMock()
                    mock_audit.close = AsyncMock()
                    mock_audit_class.return_value = mock_audit

                    with patch("verdity.cli.enrich.TokenEconomicsService"):
                        # Mock stdin for findings and diff
                        with patch("sys.stdin") as mock_stdin:
                            mock_stdin.read = MagicMock(
                                side_effect=[json.dumps(findings_data), diff]
                            )

                            result = await enrich_findings_command(mock_args)
                            assert result == 0
                            mock_enricher.enrich_findings.assert_called_once()
                            mock_gateway.close.assert_called_once()
                            mock_audit.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_enrich_findings_command_invalid_uuid(self, mock_args):
        """Test enrich_findings_command with invalid UUID."""
        mock_args.run_id = "not-a-uuid"
        mock_args.findings_file = "/tmp/test.json"
        mock_args.diff_file = "/tmp/test.diff"

        with patch("sys.stdin"):
            result = await enrich_findings_command(mock_args)
            assert result == 1

    @pytest.mark.asyncio
    async def test_enrich_findings_command_invalid_mode(self, mock_args):
        """Test enrich_findings_command with invalid mode."""
        mock_args.mode = "invalid"
        mock_args.findings_file = "/tmp/test.json"
        mock_args.diff_file = "/tmp/test.diff"

        with patch("sys.stdin"):
            result = await enrich_findings_command(mock_args)
            assert result == 1

    @pytest.mark.asyncio
    async def test_enrich_findings_command_empty_diff(self, mock_args):
        """Test enrich_findings_command with empty diff."""
        import tempfile as tf
        # Create an actual empty diff file
        with tf.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("")
            empty_diff_path = f.name
        mock_args.diff_file = empty_diff_path

        # Create a valid findings file
        findings_data = [
            {
                "finding_id": "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                "concern": "security",
                "severity": "high",
                "file": "auth/login.py",
                "line_start": 15,
                "line_end": 15,
                "summary": "Test",
                "explanation": "Test",
                "confidence": 0.5,
                "agent_version": "test",
                "prompt_hash": "",
            }
        ]
        with tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(findings_data, f)
            findings_path = f.name
        mock_args.findings_file = findings_path

        try:
            result = await enrich_findings_command(mock_args)
            assert result == 1
        finally:
            Path(empty_diff_path).unlink()
            Path(findings_path).unlink()

    @pytest.mark.asyncio
    async def test_enrich_findings_command_success(self, mock_args):
        """Test enrich_findings_command with mocked LLM gateway."""
        findings_data = [
            {
                "finding_id": "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                "concern": "security",
                "severity": "high",
                "file": "auth/login.py",
                "line_start": 15,
                "line_end": 15,
                "summary": "Test",
                "explanation": "Test",
                "confidence": 0.5,
                "agent_version": "test",
                "prompt_hash": "",
            }
        ]

        with patch("verdity.cli.enrich.LocalLLMGateway") as mock_gateway_class:
            mock_gateway = AsyncMock()
            mock_gateway_class.return_value = mock_gateway

            with patch("verdity.cli.enrich.LLMEnricherAgent") as mock_enricher_class:
                mock_enricher = AsyncMock()
                mock_enricher.enrich_findings = AsyncMock(
                    return_value=[
                        MagicMock(
                            finding_id="d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                            mode=MagicMock(value="explain"),
                            model="qwen2.5-7b-instruct",
                            backend="ollama",
                            explanation="LLM explanation",
                            confidence_adjustment_rationale="Rationale",
                            fix_diff=None,
                            fix_explanation=None,
                            risk_score=None,
                            cwe_ids=None,
                            exploitability=None,
                            blast_radius=None,
                        )
                    ]
                )
                mock_enricher_class.return_value = mock_enricher

                with patch("verdity.cli.enrich.AuditStore") as mock_audit_class:
                    mock_audit = AsyncMock()
                    mock_audit.connect = AsyncMock()
                    mock_audit.close = AsyncMock()
                    mock_audit_class.return_value = mock_audit

                    with patch("verdity.cli.enrich.TokenEconomicsService"):
                        # Use actual temp files
                        import tempfile as tf
                        with tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                            json.dump(findings_data, f)
                            findings_path = f.name
                        with tf.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                            f.write("diff content")
                            diff_path = f.name

                        mock_args.findings_file = findings_path
                        mock_args.diff_file = diff_path

                        try:
                            result = await enrich_findings_command(mock_args)
                            assert result == 0
                            mock_enricher.enrich_findings.assert_called_once()
                            mock_gateway.close.assert_called_once()
                            mock_audit.close.assert_called_once()
                        finally:
                            Path(findings_path).unlink()
                            Path(diff_path).unlink()

    @pytest.mark.asyncio
    async def test_enrich_findings_command_from_stdin(self, mock_args):
        """Test enrich_findings_command reads from stdin when no files provided."""
        import tempfile as tf

        findings_data = [
            {
                "finding_id": "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                "concern": "security",
                "severity": "high",
                "file": "auth/login.py",
                "line_start": 15,
                "line_end": 15,
                "summary": "Test",
                "explanation": "Test",
                "confidence": 0.5,
                "agent_version": "test",
                "prompt_hash": "",
            }
        ]
        diff = "diff content"

        with patch("verdity.cli.enrich.LocalLLMGateway") as mock_gateway_class:
            mock_gateway = AsyncMock()
            mock_gateway_class.return_value = mock_gateway

            with patch("verdity.cli.enrich.LLMEnricherAgent") as mock_enricher_class:
                mock_enricher = AsyncMock()
                mock_enricher.enrich_findings = AsyncMock(
                    return_value=[
                        MagicMock(
                            finding_id="d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                            mode=MagicMock(value="explain"),
                            model="qwen2.5-7b-instruct",
                            backend="ollama",
                            explanation="LLM explanation",
                            confidence_adjustment_rationale=None,
                            fix_diff=None,
                            fix_explanation=None,
                            risk_score=None,
                            cwe_ids=None,
                            exploitability=None,
                            blast_radius=None,
                        )
                    ]
                )
                mock_enricher_class.return_value = mock_enricher

                with patch("verdity.cli.enrich.AuditStore") as mock_audit_class:
                    mock_audit = AsyncMock()
                    mock_audit.connect = AsyncMock()
                    mock_audit.close = AsyncMock()
                    mock_audit_class.return_value = mock_audit

                    with patch("verdity.cli.enrich.TokenEconomicsService"):
                        # Read from stdin
                        with patch("sys.stdin") as mock_stdin:
                            mock_stdin.read = MagicMock(
                                side_effect=[json.dumps(findings_data), diff]
                            )
                            mock_args.findings_file = None
                            mock_args.diff_file = None

                            result = await enrich_findings_command(mock_args)
                            assert result == 0

    @pytest.mark.asyncio
    async def test_enrich_findings_command_output_with_all_fields(self, mock_args):
        """Test enrich_findings_command output includes all enrichment fields."""
        import tempfile as tf

        findings_data = [
            {
                "finding_id": "d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                "concern": "security",
                "severity": "high",
                "file": "auth/login.py",
                "line_start": 15,
                "line_end": 15,
                "summary": "Test",
                "explanation": "Test",
                "confidence": 0.5,
                "agent_version": "test",
                "prompt_hash": "",
            }
        ]

        with patch("verdity.cli.enrich.LocalLLMGateway") as mock_gateway_class:
            mock_gateway = AsyncMock()
            mock_gateway_class.return_value = mock_gateway

            with patch("verdity.cli.enrich.LLMEnricherAgent") as mock_enricher_class:
                mock_enricher = AsyncMock()
                mock_enricher.enrich_findings = AsyncMock(
                    return_value=[
                        MagicMock(
                            finding_id="d88f3e3a-7b2c-4d8a-9e1f-5a6b7c8d9e0f",
                            mode=MagicMock(value="all"),
                            model="qwen2.5-7b-instruct",
                            backend="ollama",
                            explanation="LLM explanation",
                            confidence_adjustment_rationale="Confidence adjusted",
                            fix_diff="def fixed(): pass",
                            fix_explanation="Fixed by...",
                            risk_score=0.8,
                            cwe_ids=["CWE-89"],
                            exploitability="easy",
                            blast_radius="local",
                        )
                    ]
                )
                mock_enricher_class.return_value = mock_enricher

                with patch("verdity.cli.enrich.AuditStore") as mock_audit_class:
                    mock_audit = AsyncMock()
                    mock_audit.connect = AsyncMock()
                    mock_audit.close = AsyncMock()
                    mock_audit_class.return_value = mock_audit

                    with patch("verdity.cli.enrich.TokenEconomicsService"):
                        with tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                            json.dump(findings_data, f)
                            findings_path = f.name
                        with tf.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                            f.write("diff content")
                            diff_path = f.name

                        mock_args.findings_file = findings_path
                        mock_args.diff_file = diff_path

                        from io import StringIO
                        import sys
                        old_stdout = sys.stdout
                        sys.stdout = stdout_capture = StringIO()

                        try:
                            result = await enrich_findings_command(mock_args)
                            assert result == 0
                            output = stdout_capture.getvalue()
                            data = json.loads(output)
                            assert len(data) == 1
                            assert data[0]["fix_diff"] == "def fixed(): pass"
                            assert data[0]["fix_explanation"] == "Fixed by..."
                            assert data[0]["risk_score"] == 0.8
                            assert data[0]["cwe_ids"] == ["CWE-89"]
                            assert data[0]["exploitability"] == "easy"
                            assert data[0]["blast_radius"] == "local"
                        finally:
                            sys.stdout = old_stdout
                            Path(findings_path).unlink()
                            Path(diff_path).unlink()


class TestMainExceptionHandling:
    """Test main() exception handling paths."""

    def test_main_keyboard_interrupt(self):
        """Test main() handles KeyboardInterrupt and returns 130."""
        from io import StringIO
        import sys

        old_argv = sys.argv
        old_stderr = sys.stderr
        sys.argv = ["verdity-enrich", "--run-id", "123e4567-e89b-12d3-a456-426614174000"]
        stderr_capture = StringIO()
        sys.stderr = stderr_capture

        try:
            with patch("verdity.cli.enrich.asyncio.run") as mock_run:
                mock_run.side_effect = KeyboardInterrupt()
                exit_code = main()
        except SystemExit as e:
            exit_code = e.code
        finally:
            sys.argv = old_argv
            sys.stderr = old_stderr

        assert exit_code == 130
        stderr_output = stderr_capture.getvalue()
        assert "Interrupted" in stderr_output

    def test_main_generic_exception(self):
        """Test main() handles generic exceptions and returns 1."""
        from io import StringIO
        import sys

        old_argv = sys.argv
        old_stderr = sys.stderr
        sys.argv = ["verdity-enrich", "--run-id", "123e4567-e89b-12d3-a456-426614174000"]
        stderr_capture = StringIO()
        sys.stderr = stderr_capture

        try:
            with patch("verdity.cli.enrich.asyncio.run") as mock_run:
                mock_run.side_effect = Exception("Something went wrong")
                exit_code = main()
        except SystemExit as e:
            exit_code = e.code
        finally:
            sys.argv = old_argv
            sys.stderr = old_stderr

        assert exit_code == 1
        stderr_output = stderr_capture.getvalue()
        assert "Error: Something went wrong" in stderr_output

    def test_main_entry_point_calls_sys_exit(self):
        """Test that running module as script calls sys.exit(main())."""
        import runpy
        import sys
        from io import StringIO
        import json

        # Provide valid stdin data to reach main() execution
        stdin_data = json.dumps([
            {
                "id": "test-finding",
                "title": "Test Finding",
                "severity": "high",
                "file": "test.py",
                "line": 10,
                "summary": "Test summary",
                "confidence": 0.9,
                "uuid": "test-uuid",
            }
        ])

        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        old_stdin = sys.stdin
        sys.argv = ["verdity.cli.enrich", "--run-id", "12345678-1234-1234-1234-123456789012", "--mode", "explain"]
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        sys.stdin = StringIO(stdin_data)

        # Other tests in this file already imported the module, so runpy would
        # re-execute an already-registered module and warn about it. Drop it
        # first so this exercises the same path a fresh interpreter would.
        saved = sys.modules.pop("verdity.cli.enrich", None)
        try:
            # sys.exit(main()) is called at the end of the if __name__ block
            try:
                runpy.run_module("verdity.cli.enrich", run_name="__main__")
            except SystemExit:
                # Expected - main() exits via sys.exit()
                pass
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            sys.stdin = old_stdin
            if saved is not None:
                sys.modules["verdity.cli.enrich"] = saved


if __name__ == "__main__":
    pytest.main([__file__, "-v"])