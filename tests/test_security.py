"""
Tests for SecurityAgent — specifically the regex-vs-substring bug fix.

The vulnerability patterns in _scan_diff_for_vulnerabilities must use regex,
not substring matching. Patterns like "subprocess.call.*shell=True" will never
match as a substring because `.*` is literal text, not a wildcard.
"""

from __future__ import annotations

import pytest

from verdity.agents.security import SecurityAgent


@pytest.fixture
def agent():
    return SecurityAgent()


class TestScanDiffForVulnerabilities:
    """Test that vulnerability patterns use regex matching."""

    def test_shell_injection_pattern_matches_with_regex(self, agent):
        """
        BUG: The pattern "subprocess.call.*shell=True" uses regex syntax (.*)
        but was matched as a literal substring. It should match when the
        code has subprocess.call with shell=True with anything in between.
        """
        diff_files = [
            {
                "path": "app.py",
                "content": "",
                "additions": 'subprocess.call(["ls"], shell=True)\n',
            }
        ]
        findings = agent._scan_diff_for_vulnerabilities(diff_files)
        # Must find the shell injection pattern
        assert any("shell injection" in f.summary.lower() for f in findings), (
            f"Expected shell injection finding, got: {[f.summary for f in findings]}"
        )

    def test_shell_injection_with_args_between(self, agent):
        """The .* should match any characters between subprocess.call and shell=True."""
        diff_files = [
            {
                "path": "app.py",
                "content": "",
                "additions": "subprocess.call(args, shell=True)\n",
            }
        ]
        findings = agent._scan_diff_for_vulnerabilities(diff_files)
        assert any("shell injection" in f.summary.lower() for f in findings)

    def test_eval_usage_detected(self, agent):
        diff_files = [
            {
                "path": "app.py",
                "content": "",
                "additions": "result = eval(user_input)\n",
            }
        ]
        findings = agent._scan_diff_for_vulnerabilities(diff_files)
        assert any("eval" in f.summary.lower() for f in findings)

    def test_sql_injection_fstring_detected(self, agent):
        diff_files = [
            {
                "path": "db.py",
                "content": "",
                "additions": 'cursor.execute(f"SELECT * FROM users WHERE id={user_id}")\n',
            }
        ]
        findings = agent._scan_diff_for_vulnerabilities(diff_files)
        assert any("sql injection" in f.summary.lower() for f in findings)

    def test_os_system_detected(self, agent):
        diff_files = [
            {
                "path": "app.py",
                "content": "",
                "additions": "os.system(user_command)\n",
            }
        ]
        findings = agent._scan_diff_for_vulnerabilities(diff_files)
        assert any(
            "os system" in f.summary.lower() or "command injection" in f.explanation.lower()
            for f in findings
        )

    def test_pickle_load_detected(self, agent):
        diff_files = [
            {
                "path": "app.py",
                "content": "",
                "additions": "data = pickle.load(f)\n",
            }
        ]
        findings = agent._scan_diff_for_vulnerabilities(diff_files)
        assert any(
            "pickle" in f.summary.lower() or "deserialization" in f.summary.lower()
            for f in findings
        )

    def test_weak_hash_detected(self, agent):
        diff_files = [
            {
                "path": "app.py",
                "content": "",
                "additions": "h = hashlib.md5(data)\n",
            }
        ]
        findings = agent._scan_diff_for_vulnerabilities(diff_files)
        assert any("weak hash" in f.summary.lower() or "md5" in f.summary.lower() for f in findings)

    def test_pattern_case_insensitive(self, agent):
        """Regex matching should be case-insensitive."""
        diff_files = [
            {
                "path": "app.py",
                "content": "",
                "additions": "EVAL(user_input)\n",
            }
        ]
        findings = agent._scan_diff_for_vulnerabilities(diff_files)
        assert any("eval" in f.summary.lower() for f in findings)

    def test_no_false_positive_on_clean_code(self, agent):
        """Clean code should not trigger findings."""
        diff_files = [
            {
                "path": "app.py",
                "content": "",
                "additions": 'result = ast.literal_eval(user_input)\nprint("hello")\n',
            }
        ]
        findings = agent._scan_diff_for_vulnerabilities(diff_files)
        # ast.literal_eval should not match eval(
        assert not any("eval" in f.summary.lower() for f in findings)


class TestScanForSecrets:
    """Secret scanning should remain as substring matching (no change needed)."""

    def test_github_token_detected(self, agent):
        diff_files = [
            {
                "path": "config.py",
                "content": "",
                "additions": 'GITHUB_TOKEN = "ghp_abc123def456"\n',
            }
        ]
        findings = agent._scan_for_secrets(diff_files)
        assert any("github" in f.summary.lower() for f in findings)

    def test_private_key_detected(self, agent):
        diff_files = [
            {
                "path": "key.pem",
                "content": "",
                "additions": "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n",
            }
        ]
        findings = agent._scan_for_secrets(diff_files)
        assert any("private key" in f.summary.lower() for f in findings)
