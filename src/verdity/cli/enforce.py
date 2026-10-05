"""
CLI for testing and validating enforcement rules.

Provides commands to validate rule files and test rules against findings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml

from verdity.enforcement import (
    Action,
    EnforcementEngine,
    GateRule,
)


@click.group()
def enforce():
    """Enforcement rules testing and validation."""
    pass


def load_rules(rules_file: Path) -> list[GateRule]:
    """Load rules from YAML file."""
    with open(rules_file) as f:
        data = yaml.safe_load(f)

    rules = []
    for rule_data in data.get("rules", []):
        # Handle action string to enum conversion
        action_str = rule_data["then"].upper()
        try:
            action = Action[action_str]
        except KeyError as exc:
            raise ValueError(
                f"Invalid action: {rule_data['then']}. Valid actions: {[a.value for a in Action]}"
            ) from exc

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


def load_finding(finding_file: Path) -> dict:
    """Load finding from JSON file."""
    with open(finding_file) as f:
        return json.load(f)


def create_finding_proxy(finding_data: dict):
    """Create a finding proxy object from dict data."""
    finding_obj = {
        "severity": finding_data.get("severity", "medium"),
        "confidence": float(finding_data.get("confidence", 0.5)),
        "concern": finding_data.get("concern", "code_quality"),
        "file": finding_data.get("file", ""),
        "line_start": finding_data.get("line_start", 0),
        "line_end": finding_data.get("line_end", 0),
        "summary": finding_data.get("summary", ""),
        "explanation": finding_data.get("explanation", ""),
        "content": finding_data.get("explanation", "")
        or finding_data.get("summary", "")
        or finding_data.get("content", ""),
    }

    class FindingProxy:
        def __init__(self, data: dict):
            self._data = data

        def __getattr__(self, name):
            return self._data.get(name)

    return FindingProxy(finding_obj)


@enforce.command()
@click.argument("rules_file", type=click.Path(exists=True, path_type=Path))
def validate(rules_file: Path):
    """Validate a rules YAML file."""
    try:
        rules = load_rules(rules_file)
        click.echo(f"✓ Rules file is valid: {len(rules)} rule(s) loaded")
        for rule in rules:
            status = "enabled" if rule.enabled else "disabled"
            click.echo(f"  - {rule.id} (priority={rule.priority}, {status}): {rule.then.value}")
    except Exception as e:
        click.echo(f"✗ Validation failed: {e}", err=True)
        sys.exit(1)


@enforce.command()
@click.argument("rules_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--finding",
    "finding_file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Finding JSON file to test against",
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed output")
@click.option(
    "--var", "variables", multiple=True, help="Variable substitutions in format key=value"
)
def test(rules_file: Path, finding_file: Path, verbose: bool, variables: tuple[str, ...]):
    """Test rules against a finding."""
    # Parse variables
    var_dict = {}
    for var in variables:
        if "=" not in var:
            click.echo(f"✗ Invalid variable format: {var}. Use key=value", err=True)
            sys.exit(1)
        key, value = var.split("=", 1)
        # Try to parse as JSON first, fallback to string
        try:
            var_dict[key] = json.loads(value)
        except json.JSONDecodeError:
            var_dict[key] = value

    try:
        rules = load_rules(rules_file)
        finding_data = load_finding(finding_file)
    except Exception as e:
        click.echo(f"✗ Failed to load files: {e}", err=True)
        sys.exit(1)

    engine = EnforcementEngine(rules=rules)
    finding_proxy = create_finding_proxy(finding_data)

    # Evaluate with context variables
    import asyncio

    async def run_evaluation():
        return await engine.evaluate_with_context(finding_proxy, var_dict)

    decision = asyncio.run(run_evaluation())

    # Output results
    if decision.blocked:
        click.echo(f"Result: {decision.action.upper()} (matched rule: {decision.rule_id})")
    else:
        click.echo("Result: ALLOW (no rules matched)")

    if decision.message:
        click.echo(f"Message: {decision.message}")

    if verbose:
        click.echo("\n--- Rule Evaluation Details ---")
        for rule in engine._get_sorted_rules():
            if not rule.enabled:
                click.echo(f"  {rule.id}: SKIPPED (disabled)")
                continue

            # Check if this rule would match
            context = {"finding": finding_proxy}
            matches = rule.evaluate(context, var_dict)
            status = "MATCHED" if matches else "no match"
            click.echo(f"  {rule.id} (priority={rule.priority}): {status}")
            if verbose and rule.when:
                when_display = rule.when
                if var_dict:
                    from verdity.enforcement import substitute_variables

                    when_display = substitute_variables(rule.when, var_dict)
                click.echo(f"    when: {when_display}")
            if matches:
                click.echo(f"    action: {rule.then.value}")
                click.echo(f"    message: {rule.substitute_message(var_dict)}")


if __name__ == "__main__":
    enforce()
