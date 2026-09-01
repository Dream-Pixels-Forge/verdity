"""Tests for Review Rules module."""
import os
import tempfile
import pytest
from pathlib import Path
from verdity.review_rules import ReviewRules, DEFAULT_RULES


class TestReviewRules:
    def test_init_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            assert rules._rules is not None
            assert rules._rules["version"] == "1.0"

    def test_load_rules_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .verdity/rules.yml
            verdity_dir = Path(tmpdir) / ".verdity"
            verdity_dir.mkdir()
            rules_file = verdity_dir / "rules.yml"
            rules_file.write_text("""
version: "1.0"
global:
  max_line_length: 100
  require_docstrings: false
agents:
  security:
    enabled: true
    min_severity: high
""")

            rules = ReviewRules(tmpdir)
            assert rules._rules is not None
            assert rules._rules["global"]["max_line_length"] == 100
            assert rules._rules["global"]["require_docstrings"] is False
            assert rules._rules["agents"]["security"]["min_severity"] == "high"

    def test_get_rules_no_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            result = rules.get_rules()
            assert result == DEFAULT_RULES

    def test_get_rules_with_file_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            verdity_dir = Path(tmpdir) / ".verdity"
            verdity_dir.mkdir()
            rules_file = verdity_dir / "rules.yml"
            rules_file.write_text("""
version: "1.0"
paths:
  "src/auth/*":
    agents:
      security:
        min_severity: critical
""")

            rules = ReviewRules(tmpdir)
            result = rules.get_rules("src/auth/login.py")
            assert result["agents"]["security"]["min_severity"] == "critical"

    def test_get_agent_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            config = rules.get_agent_config("security")
            assert "enabled" in config
            assert "min_severity" in config

    def test_should_run_agent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            assert rules.should_run_agent("security") is True
            assert rules.should_run_agent("quality") is True

    def test_get_severity_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            threshold = rules.get_severity_threshold("security")
            assert threshold in ("low", "medium", "high", "critical")

    def test_get_global_rules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            global_rules = rules.get_global_rules()
            assert "max_line_length" in global_rules
            assert "require_docstrings" in global_rules

    def test_get_language_rules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            verdity_dir = Path(tmpdir) / ".verdity"
            verdity_dir.mkdir()
            rules_file = verdity_dir / "rules.yml"
            rules_file.write_text("""
version: "1.0"
languages:
  python:
    max_line_length: 88
    require_type_hints: true
""")

            rules = ReviewRules(tmpdir)
            python_rules = rules.get_language_rules("python")
            assert python_rules["max_line_length"] == 88
            assert python_rules["require_type_hints"] is True

    def test_to_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            result = rules.to_dict()
            assert isinstance(result, dict)
            assert "version" in result
            assert "global" in result
            assert "agents" in result

    def test_detect_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            assert rules._detect_language("test.py") == "python"
            assert rules._detect_language("test.js") == "javascript"
            assert rules._detect_language("test.ts") == "typescript"
            assert rules._detect_language("test.go") == "go"
            assert rules._detect_language("test.rs") == "rust"
            assert rules._detect_language("Dockerfile") == "dockerfile"
            assert rules._detect_language("test.unknown") is None

    def test_matches_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            assert rules._matches_pattern("src/auth/login.py", "src/auth/*") is True
            assert rules._matches_pattern("src/api/test.py", "src/auth/*") is False
            assert rules._matches_pattern("test.py", "*.py") is True

    def test_merge_rules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            base = {"a": 1, "b": {"c": 2, "d": 3}}
            override = {"b": {"c": 99}, "e": 5}
            result = rules._merge_rules(base, override)
            assert result["a"] == 1
            assert result["b"]["c"] == 99
            assert result["b"]["d"] == 3
            assert result["e"] == 5


class TestReviewRulesEdgeCases:
    def test_empty_rules_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            verdity_dir = Path(tmpdir) / ".verdity"
            verdity_dir.mkdir()
            rules_file = verdity_dir / "rules.yml"
            rules_file.write_text("")

            rules = ReviewRules(tmpdir)
            assert rules._rules is not None

    def test_invalid_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            verdity_dir = Path(tmpdir) / ".verdity"
            verdity_dir.mkdir()
            rules_file = verdity_dir / "rules.yml"
            rules_file.write_text("{{invalid yaml")

            rules = ReviewRules(tmpdir)
            assert rules._rules == DEFAULT_RULES

    def test_missing_verdity_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = ReviewRules(tmpdir)
            assert rules._rules == DEFAULT_RULES

    def test_get_rules_for_different_languages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            verdity_dir = Path(tmpdir) / ".verdity"
            verdity_dir.mkdir()
            rules_file = verdity_dir / "rules.yml"
            rules_file.write_text("""
version: "1.0"
languages:
  python:
    max_line_length: 88
  javascript:
    max_line_length: 120
""")

            rules = ReviewRules(tmpdir)
            py_rules = rules.get_rules("test.py")
            js_rules = rules.get_rules("test.js")

            assert py_rules["languages"]["python"]["max_line_length"] == 88
            assert js_rules["languages"]["javascript"]["max_line_length"] == 120
