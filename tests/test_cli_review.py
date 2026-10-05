"""
Tests for verdity review CLI commands (ISS-004).

Covers:
- review command group
- diff command - PR fetching, output formatting
- run command - server initialization, review execution
- enforce command - finding loading, rule loading, variable parsing
- Output formatting (JSON/text)
- Error handling
- Main entry point
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


class TestReviewCLI:
    """Test verdity-review CLI commands."""

    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()

    def _make_finding_json(self) -> str:
        """Create a sample finding JSON content."""
        return json.dumps(
            {
                "finding_id": "test-finding-1",
                "concern": "security",
                "severity": "high",
                "file_path": "src/auth.py",
                "line_start": 10,
                "line_end": 15,
                "summary": "Hardcoded API key",
                "explanation": 'api_key = "sk_live_abc123def456"',
                "confidence": 0.85,
                "agent_version": "test@0.1.0",
                "prompt_hash": "sha256:abc123",
            }
        )

    def _make_rules_yaml(self) -> str:
        """Create a sample rules YAML content."""
        return """
rules:
  - id: block-critical-secrets
    when: "regex_search(finding.content, PATTERNS['secret'])"
    then: block
    message: "Potential secret detected in code"
    priority: 10
    enabled: true
  - id: require-approval-high
    when: "finding.severity=='high' and finding.confidence>0.7"
    then: require_approval
    message: "High severity finding requires approval"
    priority: 50
    enabled: true
"""

    def test_review_command_group_exists(self):
        """CLI should have review command group."""
        from verdity.cli.review import review

        result = self.runner.invoke(review, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "diff" in result.output
        assert "enforce" in result.output

    def test_review_main_entry_point(self):
        """Main entry point should be callable."""
        from verdity.cli.review import review

        result = self.runner.invoke(review, [])
        # Should show help when no args provided
        assert result.exit_code != 0 or "Usage" in result.output

    # ==================== diff command tests ====================

    @patch("verdity.github_client.GitHubClient")
    @patch("verdity.config.get_settings")
    def test_diff_command_json_output(self, mock_get_settings, mock_github_client):
        """diff command should output JSON when --output=json."""
        from verdity.cli.review import review

        # Setup mocks
        mock_settings = MagicMock()
        mock_settings.github_app_id = "123"
        mock_settings.github_private_key = "test-key"
        mock_settings.github_installation_id = "456"
        mock_get_settings.return_value = mock_settings

        mock_client = AsyncMock()
        mock_client.get_pr_diff.return_value = {
            "base_sha": "abc123def",
            "head_sha": "def456ghi",
            "files": [
                {"filename": "src/main.py", "additions": 10, "deletions": 5, "status": "modified"}
            ],
        }
        mock_client.close = AsyncMock()
        mock_github_client.return_value = mock_client

        result = self.runner.invoke(
            review,
            [
                "diff",
                "--owner",
                "testowner",
                "--repo",
                "testrepo",
                "--pr",
                "123",
                "--tier",
                "lite",
                "--output",
                "json",
            ],
        )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["base_sha"] == "abc123def"
        assert output["head_sha"] == "def456ghi"
        assert len(output["files"]) == 1

    @patch("verdity.github_client.GitHubClient")
    @patch("verdity.config.get_settings")
    def test_diff_command_text_output(self, mock_get_settings, mock_github_client):
        """diff command should output text when --output=text."""
        from verdity.cli.review import review

        mock_settings = MagicMock()
        mock_settings.github_app_id = "123"
        mock_settings.github_private_key = "test-key"
        mock_settings.github_installation_id = "456"
        mock_get_settings.return_value = mock_settings

        mock_client = AsyncMock()
        mock_client.get_pr_diff.return_value = {
            "base_sha": "abc123def",
            "head_sha": "def456ghi",
            "files": [
                {"filename": "src/main.py", "additions": 10, "deletions": 5, "status": "modified"}
            ],
        }
        mock_client.close = AsyncMock()
        mock_github_client.return_value = mock_client

        result = self.runner.invoke(
            review,
            [
                "diff",
                "--owner",
                "testowner",
                "--repo",
                "testrepo",
                "--pr",
                "123",
                "--output",
                "text",
            ],
        )

        assert result.exit_code == 0
        assert "PR #123 Diff:" in result.output
        assert "src/main.py" in result.output
        assert "+10/-5" in result.output

    @patch("verdity.github_client.GitHubClient")
    @patch("verdity.config.get_settings")
    def test_diff_command_failed_fetch(self, mock_get_settings, mock_github_client):
        """diff command should handle failed diff fetch."""
        from verdity.cli.review import review

        mock_settings = MagicMock()
        mock_settings.github_app_id = "123"
        mock_settings.github_private_key = "test-key"
        mock_settings.github_installation_id = "456"
        mock_get_settings.return_value = mock_settings

        mock_client = AsyncMock()
        mock_client.get_pr_diff.return_value = None
        mock_client.close = AsyncMock()
        mock_github_client.return_value = mock_client

        result = self.runner.invoke(
            review,
            [
                "diff",
                "--owner",
                "testowner",
                "--repo",
                "testrepo",
                "--pr",
                "123",
                "--output",
                "json",
            ],
        )

        assert result.exit_code == 1
        assert "Failed to fetch diff" in result.output

    def test_diff_command_missing_required_options(self):
        """diff command should require --owner, --repo, --pr."""
        from verdity.cli.review import review

        result = self.runner.invoke(review, ["diff"])
        assert result.exit_code != 0
        assert (
            "owner" in result.output.lower()
            or "repo" in result.output.lower()
            or "pr" in result.output.lower()
        )

    # ==================== run command tests ====================

    @patch("verdity.cli.review.create_mcp_server")
    def test_run_command_json_output(self, mock_create_mcp_server):
        """run command should output JSON when --output=json."""
        from verdity.cli.review import review

        mock_server = AsyncMock()
        mock_server.call_tool.return_value = {
            "pr_number": 123,
            "tier": "balanced",
            "review_run_id": "run-abc123",
            "total_findings": 2,
            "findings": [
                {"severity": "high", "file_path": "src/a.py", "line": 10, "message": "Issue 1"},
                {"severity": "medium", "file_path": "src/b.py", "line": 20, "message": "Issue 2"},
            ],
        }
        mock_server.initialize = AsyncMock()
        mock_server.shutdown = AsyncMock()
        mock_create_mcp_server.return_value = mock_server

        result = self.runner.invoke(
            review,
            [
                "run",
                "--owner",
                "testowner",
                "--repo",
                "testrepo",
                "--pr",
                "123",
                "--tier",
                "balanced",
                "--output",
                "json",
                "--no-post-comment",
                "--no-post-check",
            ],
        )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["pr_number"] == 123
        assert output["total_findings"] == 2

    @patch("verdity.cli.review.create_mcp_server")
    def test_run_command_text_output(self, mock_create_mcp_server):
        """run command should output text when --output=text."""
        from verdity.cli.review import review

        mock_server = AsyncMock()
        mock_server.call_tool.return_value = {
            "pr_number": 123,
            "tier": "balanced",
            "review_run_id": "run-abc123",
            "total_findings": 2,
            "findings": [
                {"severity": "high", "file_path": "src/a.py", "line": 10, "message": "Issue 1"},
                {"severity": "medium", "file_path": "src/b.py", "line": 20, "message": "Issue 2"},
            ],
        }
        mock_server.initialize = AsyncMock()
        mock_server.shutdown = AsyncMock()
        mock_create_mcp_server.return_value = mock_server

        result = self.runner.invoke(
            review,
            [
                "run",
                "--owner",
                "testowner",
                "--repo",
                "testrepo",
                "--pr",
                "123",
                "--output",
                "text",
                "--no-post-comment",
                "--no-post-check",
            ],
        )

        assert result.exit_code == 0
        assert "Verdity Review Results" in result.output
        assert "PR: #123" in result.output
        assert "Total Findings: 2" in result.output
        assert "HIGH" in result.output
        assert "MEDIUM" in result.output

    @patch("verdity.cli.review.create_mcp_server")
    @patch("verdity.cli.review.InspectorConfig")
    def test_run_command_with_config_file(self, mock_inspector_config, mock_create_mcp_server):
        """run command should accept --config option."""
        from verdity.cli.review import review

        mock_server = AsyncMock()
        mock_server.call_tool.return_value = {
            "pr_number": 123,
            "tier": "lite",
            "review_run_id": "run-1",
            "total_findings": 0,
            "findings": [],
        }
        mock_server.initialize = AsyncMock()
        mock_server.shutdown = AsyncMock()
        mock_create_mcp_server.return_value = mock_server

        mock_config_instance = MagicMock()
        mock_inspector_config.from_file.return_value = mock_config_instance

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("review:\n  tier: lite\n")
            config_file = f.name

        try:
            result = self.runner.invoke(
                review,
                [
                    "run",
                    "--owner",
                    "testowner",
                    "--repo",
                    "testrepo",
                    "--pr",
                    "123",
                    "--config",
                    config_file,
                    "--output",
                    "json",
                    "--no-post-comment",
                    "--no-post-check",
                ],
            )
            assert result.exit_code == 0, f"Exit code: {result.exit_code}, Output: {result.output}"
            # Path object is passed, not string
            mock_inspector_config.from_file.assert_called_once()
            called_arg = mock_inspector_config.from_file.call_args[0][0]
            assert str(called_arg) == config_file
        finally:
            Path(config_file).unlink()

    @patch("verdity.cli.review.create_mcp_server")
    def test_run_command_error_handling(self, mock_create_mcp_server):
        """run command should handle server errors."""
        from verdity.cli.review import review

        mock_server = AsyncMock()
        mock_server.call_tool.return_value = {"error": "Server error occurred"}
        mock_server.initialize = AsyncMock()
        mock_server.shutdown = AsyncMock()
        mock_create_mcp_server.return_value = mock_server

        result = self.runner.invoke(
            review,
            [
                "run",
                "--owner",
                "testowner",
                "--repo",
                "testrepo",
                "--pr",
                "123",
                "--output",
                "text",
                "--no-post-comment",
                "--no-post-check",
            ],
        )

        assert result.exit_code == 1
        assert "Error: Server error occurred" in result.output

    @patch("verdity.cli.review.create_mcp_server")
    def test_run_command_all_tiers(self, mock_create_mcp_server):
        """run command should accept all tier options."""
        from verdity.cli.review import review

        mock_server = AsyncMock()
        mock_server.call_tool.return_value = {"pr_number": 123, "total_findings": 0, "findings": []}
        mock_server.initialize = AsyncMock()
        mock_server.shutdown = AsyncMock()
        mock_create_mcp_server.return_value = mock_server

        for tier in ["lite", "balanced", "deep"]:
            result = self.runner.invoke(
                review,
                [
                    "run",
                    "--owner",
                    "testowner",
                    "--repo",
                    "testrepo",
                    "--pr",
                    "123",
                    "--tier",
                    tier,
                    "--output",
                    "json",
                    "--no-post-comment",
                    "--no-post-check",
                ],
            )
            assert result.exit_code == 0, f"Failed for tier={tier}: {result.output}"

    def test_run_command_missing_required_options(self):
        """run command should require --owner, --repo, --pr."""
        from verdity.cli.review import review

        result = self.runner.invoke(review, ["run"])
        assert result.exit_code != 0

    # ==================== enforce command tests ====================

    def test_enforce_command_with_finding_and_rules(self):
        """enforce command should evaluate finding against rules."""
        from verdity.cli.review import review

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ff:
            ff.write(self._make_finding_json())
            finding_file = ff.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as rf:
            rf.write(self._make_rules_yaml())
            rules_file = rf.name

        try:
            result = self.runner.invoke(
                review, ["enforce", finding_file, "--rules", rules_file, "--output", "json"]
            )
            assert result.exit_code == 0
            output = json.loads(result.output)
            assert "action" in output
            assert "blocked" in output
            assert "rule_id" in output
            assert "message" in output
        finally:
            Path(finding_file).unlink()
            Path(rules_file).unlink()

    def test_enforce_command_text_output(self):
        """enforce command should output text when --output=text."""
        from verdity.cli.review import review

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ff:
            ff.write(self._make_finding_json())
            finding_file = ff.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as rf:
            rf.write(self._make_rules_yaml())
            rules_file = rf.name

        try:
            result = self.runner.invoke(
                review, ["enforce", finding_file, "--rules", rules_file, "--output", "text"]
            )
            assert result.exit_code == 0
            assert "Action:" in result.output
        finally:
            Path(finding_file).unlink()
            Path(rules_file).unlink()

    def test_enforce_command_with_variables(self):
        """enforce command should support --var for variable substitution."""
        from verdity.cli.review import review

        rules_yaml = """
rules:
  - id: var-rule
    when: "finding.severity=={{sev}} and finding.confidence>{{thresh}}"
    then: block
    message: "Variable rule matched"
    priority: 10
    enabled: true
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ff:
            ff.write(self._make_finding_json())
            finding_file = ff.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as rf:
            rf.write(rules_yaml)
            rules_file = rf.name

        try:
            result = self.runner.invoke(
                review,
                [
                    "enforce",
                    finding_file,
                    "--rules",
                    rules_file,
                    "--var",
                    "sev=high",
                    "--var",
                    "thresh=0.8",
                    "--output",
                    "json",
                ],
            )
            assert result.exit_code == 0
            output = json.loads(result.output)
            assert output["rule_id"] == "var-rule"
        finally:
            Path(finding_file).unlink()
            Path(rules_file).unlink()

    def test_enforce_command_variable_json_parsing(self):
        """enforce command should parse JSON values in variables."""
        from verdity.cli.review import review

        rules_yaml = """
rules:
  - id: json-var-rule
    when: "finding.confidence > {{threshold}}"
    then: block
    message: "Threshold exceeded"
    priority: 10
    enabled: true
"""
        finding_json = json.dumps(
            {
                "severity": "medium",
                "confidence": 0.9,
                "content": "test",
                "file_path": "test.py",
                "line_start": 1,
                "line_end": 1,
                "summary": "Test",
                "explanation": "Test",
            }
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ff:
            ff.write(finding_json)
            finding_file = ff.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as rf:
            rf.write(rules_yaml)
            rules_file = rf.name

        try:
            # Pass JSON value for threshold
            result = self.runner.invoke(
                review,
                [
                    "enforce",
                    finding_file,
                    "--rules",
                    rules_file,
                    "--var",
                    "threshold=0.85",
                    "--output",
                    "json",
                ],
            )
            assert result.exit_code == 0
            output = json.loads(result.output)
            assert output["rule_id"] == "json-var-rule"
        finally:
            Path(finding_file).unlink()
            Path(rules_file).unlink()

    def test_enforce_command_invalid_variable_format(self):
        """enforce command should fail on invalid variable format."""
        from verdity.cli.review import review

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ff:
            ff.write(self._make_finding_json())
            finding_file = ff.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as rf:
            rf.write(self._make_rules_yaml())
            rules_file = rf.name

        try:
            result = self.runner.invoke(
                review,
                [
                    "enforce",
                    finding_file,
                    "--rules",
                    rules_file,
                    "--var",
                    "invalid-format",
                    "--output",
                    "json",
                ],
            )
            assert result.exit_code == 1
            assert "Invalid variable format" in result.output
        finally:
            Path(finding_file).unlink()
            Path(rules_file).unlink()

    def test_enforce_command_missing_finding_file(self):
        """enforce command should fail for missing finding file."""
        from verdity.cli.review import review

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as rf:
            rf.write(self._make_rules_yaml())
            rules_file = rf.name

        try:
            result = self.runner.invoke(
                review, ["enforce", "nonexistent.json", "--rules", rules_file]
            )
            assert result.exit_code != 0
        finally:
            Path(rules_file).unlink()

    def test_enforce_command_missing_rules_file_uses_default(self):
        """enforce command should try default rules file when --rules not provided."""
        from verdity.cli.review import review

        # Use a finding without secret patterns to avoid triggering default rules
        finding_json = json.dumps(
            {
                "severity": "low",
                "confidence": 0.3,
                "content": "just some code",
                "file_path": "test.py",
                "line_start": 1,
                "line_end": 1,
                "summary": "Style issue",
                "explanation": "Minor style issue",
            }
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ff:
            ff.write(finding_json)
            finding_file = ff.name

        try:
            result = self.runner.invoke(review, ["enforce", finding_file, "--output", "json"])
            # Should not crash, just use empty rules
            assert result.exit_code == 0
            output = json.loads(result.output)
            assert output["action"] == "ALLOW"
        finally:
            Path(finding_file).unlink()

    def test_enforce_command_default_rules_file_not_found(self):
        """enforce command should handle missing default rules file gracefully."""
        from verdity.cli.review import review

        finding_json = json.dumps(
            {
                "severity": "low",
                "confidence": 0.3,
                "content": "just some code",
                "file_path": "test.py",
                "line_start": 1,
                "line_end": 1,
                "summary": "Style issue",
                "explanation": "Minor style issue",
            }
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ff:
            ff.write(finding_json)
            finding_file = ff.name

        # Ensure .verdity/rules.yml doesn't exist

        default_rules = Path(".verdity/rules.yml")
        if default_rules.exists():
            default_rules.unlink()

        try:
            result = self.runner.invoke(review, ["enforce", finding_file, "--output", "json"])
            # Should not crash, just use empty rules
            assert result.exit_code == 0
            output = json.loads(result.output)
            assert output["action"] == "ALLOW"
        finally:
            Path(finding_file).unlink()

    def test_review_main_entry_point_direct_call(self):
        """Main entry point should be callable directly."""
        import sys

        from verdity.cli.review import review

        # Simulate calling the module directly
        old_argv = sys.argv
        sys.argv = ["review", "--help"]
        try:
            result = self.runner.invoke(review, ["--help"])
            assert result.exit_code == 0
        finally:
            sys.argv = old_argv

    def test_review_main_module_execution(self):
        """Running the module directly should work."""
        import runpy
        import sys
        from io import StringIO

        old_argv = sys.argv
        old_stdout = sys.stdout
        sys.argv = ["verdity.cli.review", "--help"]
        sys.stdout = StringIO()
        try:
            runpy.run_module("verdity.cli.review", run_name="__main__")
            output = sys.stdout.getvalue()
            assert "run" in output
            assert "diff" in output
            assert "enforce" in output
        except SystemExit:
            # --help causes SystemExit(0)
            output = sys.stdout.getvalue()
            assert "run" in output
            assert "diff" in output
            assert "enforce" in output
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout

    def test_enforce_command_invalid_finding_json(self):
        """enforce command should handle invalid JSON in finding file."""
        from verdity.cli.review import review

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ff:
            ff.write("invalid json {")
            finding_file = ff.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as rf:
            rf.write(self._make_rules_yaml())
            rules_file = rf.name

        try:
            result = self.runner.invoke(review, ["enforce", finding_file, "--rules", rules_file])
            assert result.exit_code != 0
        finally:
            Path(finding_file).unlink()
            Path(rules_file).unlink()

    def test_enforce_command_invalid_rules_yaml(self):
        """enforce command should handle invalid YAML in rules file."""
        from verdity.cli.review import review

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as ff:
            ff.write(self._make_finding_json())
            finding_file = ff.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as rf:
            rf.write("invalid: yaml: [")
            rules_file = rf.name

        try:
            result = self.runner.invoke(review, ["enforce", finding_file, "--rules", rules_file])
            assert result.exit_code != 0
        finally:
            Path(finding_file).unlink()
            Path(rules_file).unlink()

    # ==================== Output formatting tests ====================

    def test_print_text_result_no_findings(self):
        """_print_text_result should handle empty findings."""
        from verdity.cli.review import _print_text_result

        result = {
            "pr_number": 123,
            "tier": "lite",
            "review_run_id": "run-1",
            "total_findings": 0,
            "findings": [],
        }

        # Capture stdout
        import io
        import sys

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            _print_text_result(result)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        assert "No issues found" in output

    def test_print_text_result_with_findings(self):
        """_print_text_result should format findings by severity."""
        from verdity.cli.review import _print_text_result

        result = {
            "pr_number": 123,
            "tier": "balanced",
            "review_run_id": "run-1",
            "total_findings": 3,
            "findings": [
                {
                    "severity": "critical",
                    "file_path": "a.py",
                    "line": 1,
                    "message": "Critical issue",
                },
                {"severity": "high", "file_path": "b.py", "line": 2, "message": "High issue"},
                {"severity": "medium", "file_path": "c.py", "line": 3, "message": "Medium issue"},
            ],
        }

        import io
        import sys

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            _print_text_result(result)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        assert "CRITICAL" in output
        assert "HIGH" in output
        assert "MEDIUM" in output
        assert "Critical issue" in output
        assert "High issue" in output
        assert "Medium issue" in output

    def test_print_text_result_error(self):
        """_print_text_result should handle error in result."""
        from verdity.cli.review import _print_text_result

        result = {"error": "Something went wrong"}

        import io
        import sys

        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            with pytest.raises(SystemExit) as exc_info:
                _print_text_result(result)
            assert exc_info.value.code == 1
            output = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr

        assert "Error: Something went wrong" in output

    def test_create_finding_proxy_missing_fields(self):
        """_create_finding_proxy should handle missing fields with defaults."""
        from verdity.cli.review import _create_finding_proxy

        # Minimal finding data
        finding_data = {
            "severity": "low",
            "confidence": "0.3",
            "concern": "style",
            "file_path": "test.py",
            "line": 42,
            "message": "Style issue",
        }

        proxy = _create_finding_proxy(finding_data)
        assert proxy.severity == "low"
        assert proxy.confidence == 0.3
        assert proxy.concern == "style"
        assert proxy.file == "test.py"
        assert proxy.line_start == 42
        assert proxy.line_end == 42
        assert proxy.summary == "Style issue"

    def test_create_finding_proxy_all_fields(self):
        """_create_finding_proxy should use all provided fields."""
        from verdity.cli.review import _create_finding_proxy

        finding_data = {
            "severity": "critical",
            "confidence": 0.95,
            "concern": "security",
            "file_path": "src/auth.py",
            "line_start": 10,
            "line_end": 15,
            "summary": "Secret found",
            "explanation": "API key in code",
            "content": "Full content here",
        }

        proxy = _create_finding_proxy(finding_data)
        assert proxy.severity == "critical"
        assert proxy.confidence == 0.95
        assert proxy.concern == "security"
        assert proxy.file == "src/auth.py"
        assert proxy.line_start == 10
        assert proxy.line_end == 15
        assert proxy.summary == "Secret found"
        assert proxy.explanation == "API key in code"
        # content falls back to explanation if present, otherwise summary, otherwise content
        assert proxy.content == "API key in code"

    def test_create_finding_proxy_fallback_fields(self):
        """_create_finding_proxy should fallback to alternative field names."""
        from verdity.cli.review import _create_finding_proxy

        # Test with 'file' instead of 'file_path', 'line' instead of line_start/line_end
        finding_data = {"file": "src/main.py", "line": 100, "message": "Issue found"}

        proxy = _create_finding_proxy(finding_data)
        assert proxy.file == "src/main.py"
        assert proxy.line_start == 100
        assert proxy.line_end == 100
        assert proxy.summary == "Issue found"

    def test_create_finding_proxy_empty_content_fallback(self):
        """_create_finding_proxy should fallback content to explanation/summary."""
        from verdity.cli.review import _create_finding_proxy

        finding_data = {"summary": "Summary text", "explanation": "Explanation text"}

        proxy = _create_finding_proxy(finding_data)
        # content should fallback to explanation or summary
        assert proxy.content in ("Explanation text", "Summary text")


class TestReviewCLIIntegration:
    """Integration tests for review CLI."""

    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()

    @patch("verdity.cli.review.create_mcp_server")
    def test_run_command_posts_to_github_when_enabled(self, mock_create_mcp_server):
        """run command should pass post_to_github=True when comment or check enabled."""
        from verdity.cli.review import review

        mock_server = AsyncMock()
        mock_server.call_tool.return_value = {"pr_number": 123, "total_findings": 0, "findings": []}
        mock_server.initialize = AsyncMock()
        mock_server.shutdown = AsyncMock()
        mock_create_mcp_server.return_value = mock_server

        # Test with post-comment enabled
        self.runner.invoke(
            review,
            [
                "run",
                "--owner",
                "o",
                "--repo",
                "r",
                "--pr",
                "1",
                "--post-comment",
                "--no-post-check",
                "--output",
                "json",
            ],
        )
        call_args = mock_server.call_tool.call_args
        assert call_args is not None
        args, kwargs = call_args
        params = args[1]  # Second positional arg is the params dict
        assert params["post_to_github"] is True

        # Test with post-check enabled
        mock_server.call_tool.reset_mock()
        self.runner.invoke(
            review,
            [
                "run",
                "--owner",
                "o",
                "--repo",
                "r",
                "--pr",
                "1",
                "--no-post-comment",
                "--post-check",
                "--output",
                "json",
            ],
        )
        call_args = mock_server.call_tool.call_args
        assert call_args is not None
        args, kwargs = call_args
        params = args[1]
        assert params["post_to_github"] is True

        # Test with both disabled
        mock_server.call_tool.reset_mock()
        self.runner.invoke(
            review,
            [
                "run",
                "--owner",
                "o",
                "--repo",
                "r",
                "--pr",
                "1",
                "--no-post-comment",
                "--no-post-check",
                "--output",
                "json",
            ],
        )
        call_args = mock_server.call_tool.call_args
        assert call_args is not None
        args, kwargs = call_args
        params = args[1]
        assert params["post_to_github"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
