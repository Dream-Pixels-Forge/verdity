"""
Enforcement engine for Verdity - blocking rules and gate actions.

Provides a rules engine that evaluates findings against configurable blocking rules
before they reach the confidence threshold router.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Action(Enum):
    """Enforcement action types."""

    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"
    ESCALATE = "escalate"


@dataclass
class GateRule:
    """A single enforcement gate rule."""

    id: str
    when: str  # Python expression (restricted eval)
    then: Action
    message: str

    def evaluate(self, context: dict[str, Any]) -> bool:
        """Evaluate the rule against a context using restricted eval."""
        try:
            # Use a restricted eval with only safe built-ins
            allowed_names = {
                "finding": context.get("finding", {}),
            }
            code = compile(self.when, "<rule>", "eval")
            result = eval(code, {"__builtins__": {}}, allowed_names)
            return bool(result)
        except Exception as e:
            logger.warning("Rule %s evaluation failed: %s", self.id, e)
            return False


@dataclass
class EnforcementDecision:
    """Result of enforcement evaluation."""

    action: str  # "ALLOW", "BLOCK", "REQUIRE_APPROVAL", "ESCALATE"
    rule_id: str | None = None
    message: str = ""

    @property
    def blocked(self) -> bool:
        return self.action.upper() in ("BLOCK", "REQUIRE_APPROVAL", "ESCALATE")


class EnforcementEngine:
    """
    Evaluates findings against a set of blocking rules.

    Rules are evaluated in order; first matching rule determines the action.
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

    async def evaluate(self, finding: Any) -> EnforcementDecision:
        """
        Evaluate a finding against all rules.

        Args:
            finding: Finding object with attributes like severity, confidence, concern

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
        }

        class FindingProxy:
            """Proxy to allow attribute-style access to finding dict."""
            def __init__(self, data: dict):
                self._data = data
            def __getattr__(self, name):
                return self._data.get(name)

        finding_proxy = FindingProxy(finding_obj)
        context = {"finding": finding_proxy}

        for rule in self.rules:
            if rule.evaluate(context):
                logger.info("Enforcement rule %s matched finding %s", rule.id, finding.finding_id)
                return EnforcementDecision(
                    action=rule.then.value,
                    rule_id=rule.id,
                    message=rule.message,
                )

        return EnforcementDecision(action="allow")