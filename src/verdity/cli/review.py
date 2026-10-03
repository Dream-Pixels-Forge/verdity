"""
CLI for running Verdity reviews on GitHub PRs.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from verdity.config import InspectorConfig
from verdity.mcp_server import create_mcp_server
from verdity.model_fallback import MultiModelFallback


@click.group()
def review():
    """Verdity PR review commands."""
    pass


@review.command()
@click.option("--owner", required=True, help="Repository owner (user or org)")
@click.option("--repo", required=True, help="Repository name")
@click.option("--pr", "pr_number", required=True, type=int, help="Pull request number")
@click.option(
    "--tier",
    type=click.Choice(["lite", "balanced", "deep"]),
    default="balanced",
    help="Review tier: lite (fast), balanced (default), deep (full context)",
)
@click.option(
    "--post-comment/--no-post-comment",
    default=True,
    help="Post results as PR comment",
)
@click.option(
    "--post-check/--no-post-check",
    default=True,
    help="Post results as GitHub Check Run",
)
@click.option(
    "--only-changed-files/--all-files",
    default=True,
    help="Only review changed files",
)
@click.option(
    "--output",
    type=click.Choice(["json", "text"]),
    default="json",
    help="Output format",
)
@click.option(
    "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to Verdity config file",
)
def run(
    owner: str,
    repo: str,
    pr_number: int,
    tier: str,
    post_comment: bool,
    post_check: bool,
    only_changed_files: bool,
    output: str,
    config: Path | None,
):
    """Run a full Verdity review on a GitHub PR."""

    async def _run():
        server = create_mcp_server(
            config=InspectorConfig() if not config else InspectorConfig.from_file(config),
            multi_model=MultiModelFallback(),
        )
        await server.initialize()

        result = await server.call_tool("verdity_review", {
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "tier": tier,
            "post_to_github": post_check or post_comment,
        })

        await server.shutdown()

        if output == "json":
            click.echo(json.dumps(result, indent=2))
        else:
            _print_text_result(result)

    asyncio.run(_run())


def _print_text_result(result: dict):
    """Print review result in human-readable format."""
    if "error" in result:
        click.echo(f"Error: {result['error']}", err=True)
        sys.exit(1)

    click.echo(f"\n=== Verdity Review Results ===")
    click.echo(f"PR: #{result.get('pr_number', 'unknown')}")
    click.echo(f"Tier: {result.get('tier', 'unknown')}")
    click.echo(f"Review Run ID: {result.get('review_run_id', 'unknown')}")
    click.echo(f"Total Findings: {result.get('total_findings', 0)}")
    click.echo()

    findings = result.get("findings", [])
    if not findings:
        click.echo("No issues found! 🎉")
        return

    # Group by severity
    from collections import defaultdict
    by_severity = defaultdict(list)
    for f in findings:
        by_severity[f.get("severity", "unknown")].append(f)

    severity_order = ["critical", "high", "medium", "low", "info"]
    for sev in severity_order:
        if sev in by_severity:
            click.echo(f"\n--- {sev.upper()} ({len(by_severity[sev])}) ---")
            for f in by_severity[sev]:
                click.echo(f"  {f.get('file_path', 'unknown')}:{f.get('line', 0)} - {f.get('message', 'No message')}")


@review.command()
@click.option("--owner", required=True, help="Repository owner")
@click.option("--repo", required=True, help="Repository name")
@click.option("--pr", "pr_number", required=True, type=int, help="PR number")
@click.option("--tier", type=click.Choice(["lite", "balanced", "deep"]), default="balanced")
@click.option("--output", type=click.Choice(["json", "text"]), default="json")
def diff(owner: str, repo: str, pr_number: int, tier: str, output: str):
    """Fetch and display PR diff (for debugging)."""
    from verdity.github_client import GitHubClient
    from verdity.config import get_settings

    async def _run():
        settings = get_settings()
        client = GitHubClient(
            app_id=settings.github_app_id,
            private_key_pem=settings.github_private_key,
            installation_id=settings.github_installation_id,
        )

        diff = await client.get_pr_diff(owner, repo, pr_number)
        await client.close()

        if not diff:
            click.echo("Failed to fetch diff", err=True)
            sys.exit(1)

        if output == "json":
            click.echo(json.dumps(diff, indent=2))
        else:
            click.echo(f"PR #{pr_number} Diff:")
            click.echo(f"  Base SHA: {diff['base_sha'][:8]}")
            click.echo(f"  Head SHA: {diff['head_sha'][:8]}")
            click.echo(f"  Files changed: {len(diff['files'])}")
            for f in diff["files"]:
                click.echo(f"  - {f['filename']} (+{f['additions']}/-{f['deletions']}) [{f['status']}]")

    asyncio.run(_run())


@review.command()
@click.argument("finding_file", type=click.Path(exists=True, path_type=Path))
@click.option("--rules", "rules_file", type=click.Path(exists=True, path_type=Path), help="Custom rules YAML file")
@click.option("--var", "variables", multiple=True, help="Variables in format key=value")
@click.option("--output", type=click.Choice(["json", "text"]), default="json")
def enforce(finding_file: Path, rules_file: Path | None, variables: tuple[str, ...], output: str):
    """Test a finding against enforcement rules."""
    from verdity.enforcement import EnforcementEngine, GateRule, Action, load_rules_from_yaml

    # Parse variables
    var_dict = {}
    for var in variables:
        if "=" not in var:
            click.echo(f"Invalid variable format: {var}. Use key=value", err=True)
            sys.exit(1)
        key, value = var.split("=", 1)
        try:
            var_dict[key] = json.loads(value)
        except json.JSONDecodeError:
            var_dict[key] = value

    # Load finding
    with open(finding_file) as f:
        finding_data = json.load(f)

    # Load rules
    if rules_file:
        rules = load_rules_from_yaml(str(rules_file))
    else:
        try:
            rules = load_rules_from_yaml(".verdity/rules.yml")
        except Exception:
            rules = []

    engine = EnforcementEngine(rules=rules)
    finding_proxy = _create_finding_proxy(finding_data)

    import asyncio
    decision = asyncio.run(engine.evaluate_with_context(finding_proxy, var_dict))

    if output == "json":
        click.echo(json.dumps({
            "action": decision.action.upper(),
            "blocked": decision.blocked,
            "rule_id": decision.rule_id,
            "message": decision.message,
        }, indent=2))
    else:
        click.echo(f"Action: {decision.action.upper()}")
        if decision.blocked:
            click.echo(f"Rule: {decision.rule_id}")
            click.echo(f"Message: {decision.message}")


def _create_finding_proxy(finding_data: dict) -> object:
    """Create a finding proxy object from dict data for evaluation."""
    finding_obj = {
        "severity": finding_data.get("severity", "medium"),
        "confidence": float(finding_data.get("confidence", 0.5)),
        "concern": finding_data.get("concern", "code_quality"),
        "file": finding_data.get("file_path", finding_data.get("file", "")),
        "line_start": finding_data.get("line_start", finding_data.get("line", 0)),
        "line_end": finding_data.get("line_end", finding_data.get("line", 0)),
        "summary": finding_data.get("summary", finding_data.get("message", "")),
        "explanation": finding_data.get("explanation", ""),
        "content": finding_data.get("explanation", "") or finding_data.get("summary", "") or finding_data.get("content", ""),
    }

    class FindingProxy:
        def __init__(self, data: dict):
            self._data = data

        def __getattr__(self, name):
            return self._data.get(name)

    return FindingProxy(finding_obj)


if __name__ == "__main__":
    review()
