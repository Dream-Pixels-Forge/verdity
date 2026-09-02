"""
Verdity Review Rules — Custom project-specific review configuration.

Supports `.verdity/rules.yml` for custom rules per language/framework.
Copilot reads `.github/copilot-instructions.md`, `AGENTS.md`, `REVIEW.md`.
Qodo has a rules engine. Verdity uses YAML for structured, machine-readable rules.

Rule file location: `.verdity/rules.yml` in repository root.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_RULES: Dict[str, Any] = {
    "version": "1.0",
    "global": {
        "max_line_length": 120,
        "require_docstrings": True,
        "require_type_hints": True,
        "require_tests": True,
    },
    "languages": {},
    "paths": {},
    "agents": {
        "security": {"enabled": True, "min_severity": "medium"},
        "quality": {"enabled": True, "min_severity": "low"},
        "testing": {"enabled": True, "min_severity": "medium"},
        "documentation": {"enabled": True, "min_severity": "low"},
    },
}


class ReviewRules:
    """Load and apply custom review rules for a repository."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)
        self._rules: Optional[Dict[str, Any]] = None
        self._load_rules()

    def _load_rules(self) -> None:
        """Load rules from .verdity/rules.yml"""
        rules_file = self.repo_path / ".verdity" / "rules.yml"
        if not rules_file.exists():
            logger.debug("No rules file found at %s, using defaults", rules_file)
            self._rules = DEFAULT_RULES.copy()
            return

        try:
            with open(rules_file, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if loaded is None:
                    self._rules = DEFAULT_RULES.copy()
                else:
                    self._rules = self._merge_rules(DEFAULT_RULES, loaded)
            logger.info("Loaded review rules from %s", rules_file)
        except yaml.YAMLError as e:
            logger.warning("Failed to parse rules file %s: %s", rules_file, e)
            self._rules = DEFAULT_RULES.copy()
        except Exception as e:
            logger.warning("Failed to load rules file %s: %s", rules_file, e)
            self._rules = DEFAULT_RULES.copy()

    def _merge_rules(
        self, base: Dict[str, Any], override: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Deep merge override into base rules."""
        result = base.copy()
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._merge_rules(result[key], value)
            else:
                result[key] = value
        return result

    def get_rules(self, file_path: str = "") -> Dict[str, Any]:
        """Get applicable rules for a file path."""
        if not self._rules:
            return DEFAULT_RULES.copy()

        if not file_path:
            return self._rules

        applicable = self._rules.copy()

        # Apply path-specific rules
        path_rules = self._rules.get("paths", {})
        for pattern, rules in path_rules.items():
            if self._matches_pattern(file_path, pattern):
                applicable = self._merge_rules(applicable, rules)

        # Apply language-specific rules
        language = self._detect_language(file_path)
        if language and language in self._rules.get("languages", {}):
            lang_rules = self._rules["languages"][language]
            applicable = self._merge_rules(applicable, lang_rules)

        return applicable

    def _matches_pattern(self, file_path: str, pattern: str) -> bool:
        """Check if a file path matches a pattern (glob-style)."""
        from fnmatch import fnmatch

        return fnmatch(file_path, pattern)

    def _detect_language(self, file_path: str) -> Optional[str]:
        """Detect programming language from file extension."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".jsx": "javascript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".rb": "ruby",
            ".php": "php",
            ".cs": "csharp",
            ".cpp": "cpp",
            ".c": "c",
            ".h": "c",
            ".hpp": "cpp",
            ".swift": "swift",
            ".kt": "kotlin",
            ".scala": "scala",
            ".sql": "sql",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".json": "json",
            ".toml": "toml",
            ".md": "markdown",
            ".sh": "shell",
            ".bash": "shell",
            ".dockerfile": "dockerfile",
            ".tf": "terraform",
            ".hcl": "terraform",
        }

        path = Path(file_path)
        ext = path.suffix.lower()
        if ext in ext_map:
            return ext_map[ext]

        # Check for Dockerfile
        if path.name.lower() == "dockerfile":
            return "dockerfile"

        return None

    def get_agent_config(self, agent_name: str) -> Dict[str, Any]:
        """Get configuration for a specific agent."""
        if not self._rules:
            return {"enabled": True, "min_severity": "low"}

        return self._rules.get("agents", {}).get(
            agent_name, {"enabled": True, "min_severity": "low"}
        )

    def should_run_agent(self, agent_name: str) -> bool:
        """Check if an agent should run based on rules."""
        config = self.get_agent_config(agent_name)
        return config.get("enabled", True)

    def get_severity_threshold(self, agent_name: str) -> str:
        """Get minimum severity threshold for an agent."""
        config = self.get_agent_config(agent_name)
        return config.get("min_severity", "low")

    def get_global_rules(self) -> Dict[str, Any]:
        """Get global review rules."""
        if not self._rules:
            return DEFAULT_RULES.get("global", {})
        return self._rules.get("global", {})

    def get_language_rules(self, language: str) -> Dict[str, Any]:
        """Get rules for a specific language."""
        if not self._rules:
            return {}
        return self._rules.get("languages", {}).get(language, {})

    def to_dict(self) -> Dict[str, Any]:
        """Export rules as dictionary."""
        return self._rules or DEFAULT_RULES.copy()
