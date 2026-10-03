"""
Enforcement engine for Verdity - blocking rules and gate actions.

Provides a rules engine that evaluates findings against configurable blocking rules
before they reach the confidence threshold router.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Action(Enum):
    """Enforcement action types."""

    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"
    ESCALATE = "escalate"


# Built-in regex pattern library - use a class for attribute access
class _Patterns:
    """Wrapper to allow attribute-style access to patterns."""

    secret = r"(api[_-]?key|secret|token|password)\s*[=:]\s*['\"][^'\"]+['\"]"
    sql_injection = r"(?i)(union|select|insert|update|delete|drop)\b"
    xss = r"(?i)(<script|onerror=|onclick=|onload=)"
    path_traversal = r"\.\.[/\\]"


PATTERNS = _Patterns()


def regex_search(text: str | None, pattern: str) -> bool:
    """Search for regex pattern in text. Returns False if text is None."""
    if text is None:
        return False
    return re.search(pattern, text) is not None


def substitute_variables(template: str, variables: dict[str, Any]) -> str:
    """Substitute {{variable}} placeholders in template with values from variables dict."""
    if not variables:
        return template

    result = template
    for key, value in variables.items():
        placeholder = f"{{{{{key}}}}}"
        # Convert value to string representation suitable for Python eval
        if isinstance(value, str):
            str_value = f"'{value}'"
        elif isinstance(value, bool):
            str_value = str(value)
        elif value is None:
            str_value = "None"
        else:
            str_value = str(value)
        result = result.replace(placeholder, str_value)
    return result


@dataclass
class GateRule:
    """A single enforcement gate rule."""

    id: str
    when: str  # Python expression (restricted eval) with {{variable}} support
    then: Action
    message: str
    priority: int = 100  # Lower = higher priority
    enabled: bool = True

    def evaluate(self, context: dict[str, Any], variables: dict[str, Any] | None = None) -> bool:
        """Evaluate the rule against a context using restricted eval with variable substitution."""
        try:
            # Substitute variables in the when clause
            when_expr = substitute_variables(self.when, variables or {})

            # Build allowed names for eval
            allowed_names = {
                "finding": context.get("finding", {}),
                "regex_search": regex_search,
                "PATTERNS": PATTERNS,
            }

            code = compile(when_expr, "<rule>", "eval")
            result = eval(code, {"__builtins__": {}}, allowed_names)
            return bool(result)
        except Exception as e:
            logger.warning("Rule %s evaluation failed: %s", self.id, e)
            return False

    def substitute_message(self, variables: dict[str, Any] | None = None) -> str:
        """Substitute variables in the message."""
        return substitute_variables(self.message, variables or {})


@dataclass
class EnforcementDecision:
    """Result of enforcement evaluation."""

    action: str  # "ALLOW", "BLOCK", "REQUIRE_APPROVAL", "ESCALATE"
    rule_id: str | None = None
    message: str = ""

    @property
    def blocked(self) -> bool:
        return self.action.upper() in ("BLOCK", "REQUIRE_APPROVAL", "ESCALATE")


@dataclass
class RuleSet:
    """A named group of enforcement rules."""

    name: str
    rules: list[GateRule]
    description: str = ""

    def evaluate(self, finding: Any, context: dict[str, Any] | None = None) -> list[EnforcementDecision]:
        """Evaluate all rules in priority order against a finding."""
        # Sort rules by priority
        sorted_rules = sorted(self.rules, key=lambda r: (r.priority, self.rules.index(r)))
        decisions = []

        # Build finding proxy
        finding_obj = {
            "severity": finding.severity.value if hasattr(finding.severity, "value") else str(finding.severity),
            "confidence": float(finding.confidence),
            "concern": finding.concern.value if hasattr(finding.concern, "value") else str(finding.concern),
            "file": finding.file,
            "line_start": finding.line_start,
            "line_end": finding.line_end,
            "summary": finding.summary,
            "explanation": getattr(finding, "explanation", ""),
            "content": getattr(finding, "explanation", "") or getattr(finding, "summary", ""),
        }

        class FindingProxy:
            def __init__(self, data: dict):
                self._data = data

            def __getattr__(self, name):
                return self._data.get(name)

        finding_proxy = FindingProxy(finding_obj)
        eval_context = {"finding": finding_proxy}

        variables = context or {}

        for rule in sorted_rules:
            if not rule.enabled:
                continue
            if rule.evaluate(eval_context, variables):
                logger.info("RuleSet %s: rule %s matched", self.name, rule.id)
                decisions.append(
                    EnforcementDecision(
                        action=rule.then.value,
                        rule_id=rule.id,
                        message=rule.substitute_message({**variables, **finding_obj}),
                    )
                )

        return decisions


class EnforcementEngine:
    """
    Evaluates findings against a set of blocking rules.

    Rules are evaluated in priority order (lower priority number = higher priority);
    first matching rule determines the action.
    If no rules match, the finding is ALLOWED.
    """

    def __init__(self, rules: list[GateRule] | None = None):
        self.rules: list[GateRule] = rules or []

    def add_rule(self, rule: GateRule) -> None:
        """Add a rule to the engine."""
        self.rules.append(rule)

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID."""
        for i, rule in enumerate(self.rules):
            if rule.id == rule_id:
                self.rules.pop(i)
                return True
        return False

    def _get_sorted_rules(self) -> list[GateRule]:
        """Get rules sorted by priority (lower = higher priority), then insertion order."""
        return sorted(self.rules, key=lambda r: (r.priority, self.rules.index(r)))

    async def evaluate(self, finding: Any) -> EnforcementDecision:
        """
        Evaluate a finding against all rules.

        Args:
            finding: Finding object with attributes like severity, confidence, concern

        Returns:
            EnforcementDecision with action and matching rule info
        """
        return await self.evaluate_with_context(finding, {})

    async def evaluate_with_context(self, finding: Any, variables: dict[str, Any] | None = None) -> EnforcementDecision:
        """
        Evaluate a finding against all rules with additional context variables.

        Args:
            finding: Finding object with attributes like severity, confidence, concern
            variables: Optional dict of variables for template substitution

        Returns:
            EnforcementDecision with action and matching rule info
        """
        # Build context for evaluation
        finding_obj = {
            "severity": finding.severity.value if hasattr(finding.severity, "value") else str(finding.severity),
            "confidence": float(finding.confidence),
            "concern": finding.concern.value if hasattr(finding.concern, "value") else str(finding.concern),
            "file": finding.file,
            "line_start": finding.line_start,
            "line_end": finding.line_end,
            "summary": finding.summary,
            "explanation": getattr(finding, "explanation", ""),
            "content": getattr(finding, "explanation", "") or getattr(finding, "summary", ""),
        }

        class FindingProxy:
            """Proxy to allow attribute-style access to finding dict."""

            def __init__(self, data: dict):
                self._data = data

            def __getattr__(self, name):
                return self._data.get(name)

        finding_proxy = FindingProxy(finding_obj)
        context = {"finding": finding_proxy}

        variables = variables or {}

        for rule in self._get_sorted_rules():
            if not rule.enabled:
                continue
            if rule.evaluate(context, variables):
                logger.info("Enforcement rule %s matched finding %s", rule.id, finding.finding_id)
                return EnforcementDecision(
                    action=rule.then.value,
                    rule_id=rule.id,
                    message=rule.substitute_message({**variables, **finding_obj}),
                )

        return EnforcementDecision(action="allow")


def load_rules_from_yaml(rules_file: str) -> list[GateRule]:
    """Load rules from YAML file."""
    import yaml

    with open(rules_file) as f:
        data = yaml.safe_load(f)

    rules = []
    for rule_data in data.get("rules", []):
        action_str = rule_data["then"].upper()
        action = Action[action_str]
        rule = GateRule(
            id=rule_data["id"],
            when=rule_data["when"],
            then=action,
            message=rule_data.get("message", ""),
            priority=rule_data.get("priority", 100),
            enabled=rule_data.get("enabled", True),
        )
        rules.append(rule)

    return rules
