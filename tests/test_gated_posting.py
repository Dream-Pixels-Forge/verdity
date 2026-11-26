"""SG-17 — every PR post goes through aggregate → route → approval queue.

Non-negotiable constraint #7: sub-threshold findings never auto-post; they
always go to the Approval Queue. These tests pin that on the gate itself and
on the one production path that writes to a PR (MCP ``verdity_review``, which
``verdity-review run --post-check/--post-comment`` also uses).
"""

from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from verdity.approval_queue import ApprovalQueue
from verdity.audit_store import AuditStore
from verdity.enforcement import Action, EnforcementEngine, GateRule
from verdity.gating import GateResult, gate_findings
from verdity.mcp_server import MCPServer
from verdity.router import RouteAction
from verdity.schemas import ConcernType, Finding, RepoRef, Severity

REPO = RepoRef(owner="acme", name="api", id=1)


def _finding(**kw) -> Finding:
    base = {
        "concern": ConcernType.SECURITY,
        "severity": Severity.HIGH,
        "file": "app.py",
        "line_start": 10,
        "line_end": 10,
        "summary": "s",
        "explanation": "e",
        "confidence": 0.9,
        "agent_version": "t@1",
        "prompt_hash": "h",
    }
    base.update(kw)
    return Finding(**base)


# Composite confidence = base * (1 - sev_weight) + sev_weight + concern_boost
def _high() -> Finding:  # 0.9*0.2 + 0.8 + 0.15 → 1.0  → auto_approve
    return _finding(summary="Hard-coded AWS key", file="cfg.py", line_start=3, line_end=3)


def _medium() -> Finding:  # 0.5*0.7 + 0.3 + 0.0 = 0.65 → manual_review
    return _finding(
        concern=ConcernType.CODE_QUALITY,
        severity=Severity.LOW,
        confidence=0.5,
        summary="Function is long",
        file="svc.py",
        line_start=40,
        line_end=60,
    )


def _low() -> Finding:  # 0.3*0.9 + 0.1 + 0.0 = 0.37 → auto_dismiss
    return _finding(
        concern=ConcernType.DOCUMENTATION,
        severity=Severity.INFO,
        confidence=0.3,
        summary="Missing docstring",
        file="util.py",
        line_start=1,
        line_end=1,
    )


def _resp(findings):
    return SimpleNamespace(findings=findings)


@pytest_asyncio.fixture
async def stores():
    audit = AuditStore()
    queue = ApprovalQueue()
    await audit.connect()
    await queue.connect()
    yield audit, queue
    await audit.close()
    await queue.close()


# ── gate_findings ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_splits_post_queue_dismiss(stores):
    audit, queue = stores
    run_id = uuid.uuid4()

    result = await gate_findings(
        review_run_id=run_id,
        repo=REPO,
        responses=[_resp([_high(), _medium(), _low()])],
        approval_queue=queue,
        audit=audit,
    )

    assert [f.summary for f in result.postable] == ["Hard-coded AWS key"]
    assert [g.finding.summary for g in result.queued] == ["Function is long"]
    assert [g.finding.summary for g in result.dismissed] == ["Missing docstring"]
    assert result.blocking == []
    assert result.counts() == {"posted": 1, "queued": 1, "dismissed": 1, "blocking": 0}
    assert result.conclusion == "neutral"  # something is waiting on a human

    pending = await queue.get_pending(repo_id="acme/api")
    assert [p["summary"] for p in pending] == ["Function is long"]
    assert pending[0]["route_action"] == RouteAction.MANUAL_REVIEW.value

    routed = [r for r in await audit.query_by_run(run_id) if r["event_type"] == "finding.routed"]
    actions = sorted(json.loads(r["payload"])["action"] for r in routed)
    assert actions == ["auto_approve", "auto_dismiss", "manual_review"]


@pytest.mark.asyncio
async def test_gate_dedups_same_location_across_specialists():
    a = _high()
    b = _high().model_copy(update={"finding_id": uuid.uuid4(), "summary": "dup"})

    result = await gate_findings(
        review_run_id=uuid.uuid4(), repo=REPO, responses=[_resp([a]), _resp([b])]
    )

    assert len(result.postable) == 1


@pytest.mark.asyncio
async def test_gate_does_not_prefilter_on_raw_confidence():
    # Raw confidence 0.5 but CRITICAL: the router scores it ≥ 0.9. The
    # aggregator's own 0.7 raw filter must not drop it before routing.
    crit = _finding(severity=Severity.CRITICAL, confidence=0.5)

    result = await gate_findings(review_run_id=uuid.uuid4(), repo=REPO, responses=[_resp([crit])])

    assert result.postable == [crit]


@pytest.mark.asyncio
async def test_block_rule_fails_the_check_and_is_posted():
    engine = EnforcementEngine(
        rules=[
            GateRule(
                id="no-secrets",
                when="finding.concern=='security'",
                then=Action.BLOCK,
                message="Secrets block merge",
            )
        ]
    )

    result = await gate_findings(
        review_run_id=uuid.uuid4(),
        repo=REPO,
        responses=[_resp([_high(), _medium()])],
        enforcement=engine,
    )

    assert [g.finding.summary for g in result.blocking] == ["Hard-coded AWS key"]
    assert result.blocking[0].decision.action == RouteAction.BLOCK
    assert "Hard-coded AWS key" in [f.summary for f in result.postable]
    assert result.conclusion == "failure"
    assert "Secrets block merge" in result.summary_markdown


@pytest.mark.asyncio
async def test_require_approval_rule_queues_even_high_confidence(stores):
    _, queue = stores
    engine = EnforcementEngine(
        rules=[
            GateRule(
                id="human-on-security",
                when="finding.concern=='security'",
                then=Action.REQUIRE_APPROVAL,
                message="Security needs a human",
            )
        ]
    )

    result = await gate_findings(
        review_run_id=uuid.uuid4(),
        repo=REPO,
        responses=[_resp([_high()])],
        enforcement=engine,
        approval_queue=queue,
    )

    assert result.postable == []
    assert len(result.queued) == 1
    assert len(await queue.get_pending()) == 1


@pytest.mark.asyncio
async def test_clean_run_is_success():
    result = await gate_findings(review_run_id=uuid.uuid4(), repo=REPO, responses=[_resp([])])
    assert result == GateResult(postable=[], queued=[], dismissed=[], blocking=[])
    assert result.conclusion == "success"


@pytest.mark.asyncio
async def test_non_finding_objects_are_skipped_not_posted():
    junk = MagicMock()  # e.g. a malformed specialist result
    result = await gate_findings(
        review_run_id=uuid.uuid4(), repo=REPO, responses=[_resp([junk, _high()])]
    )
    assert len(result.postable) == 1
    assert result.skipped == 1


@pytest.mark.asyncio
async def test_queued_findings_are_reported_without_a_store():
    # No approval queue configured: the finding is still held back (never
    # posted) and is visible in the result.
    result = await gate_findings(
        review_run_id=uuid.uuid4(), repo=REPO, responses=[_resp([_medium()])]
    )
    assert result.postable == []
    assert len(result.queued) == 1


# ── MCP verdity_review (the CLI's post path) ──────────────────────────


@pytest.mark.asyncio
async def test_verdity_review_posts_only_gated_findings():
    server = MCPServer()
    audit, queue = AuditStore(), ApprovalQueue()
    await audit.connect()
    await queue.connect()
    server._audit, server._approval_queue = audit, queue

    run = MagicMock()
    run.specialist_results = {
        "security": _resp([_high()]),
        "code_quality": _resp([_medium()]),
        "documentation": _resp([_low()]),
    }
    with (
        patch("verdity.github_client.GitHubClient") as gh_cls,
        patch("verdity.config.get_settings"),
        patch.object(server, "_orchestrator") as orch,
    ):
        gh = AsyncMock()
        gh.get_pr_diff.return_value = {
            "files": [{"filename": "cfg.py", "patch": "+x", "additions": 1, "deletions": 0}],
            "base_sha": "b",
            "head_sha": "h",
        }
        gh.create_check_run.return_value = {"id": 7}
        gh_cls.return_value = gh
        orch.process_event = AsyncMock(return_value=uuid.uuid4())
        orch.get_run = MagicMock(return_value=run)

        result = await server.call_tool(
            "verdity_review",
            {"owner": "acme", "repo": "api", "pr_number": 5, "post_to_github": True},
        )

    assert "error" not in result, result.get("error")
    # The caller still sees every finding the specialists produced …
    assert result["total_findings"] == 3
    # … but only the one that earned it reaches the PR.
    assert result["routing"] == {"posted": 1, "queued": 1, "dismissed": 1, "blocking": 0}
    assert [q["message"] for q in result["queued_findings"]] == ["Function is long"]

    kwargs = gh.create_check_run.call_args.kwargs
    assert [a["message"] for a in kwargs["output"]["annotations"]] == ["Hard-coded AWS key"]
    assert "Held for human review: **1**" in kwargs["output"]["summary"]
    # A check run left "in_progress" spins forever on GitHub.
    assert kwargs["status"] == "completed"
    assert kwargs["conclusion"] == "neutral"
    assert kwargs["completed_at"]
    assert len(await queue.get_pending()) == 1

    await audit.close()
    await queue.close()


@pytest.mark.asyncio
async def test_verdity_review_without_post_still_reports_routing():
    server = MCPServer()
    run = MagicMock()
    run.specialist_results = {"security": _resp([_high(), _low()])}
    with (
        patch("verdity.github_client.GitHubClient") as gh_cls,
        patch("verdity.config.get_settings"),
        patch.object(server, "_orchestrator") as orch,
    ):
        gh = AsyncMock()
        gh.get_pr_diff.return_value = {"files": [], "base_sha": "b", "head_sha": "h"}
        gh_cls.return_value = gh
        orch.process_event = AsyncMock(return_value=uuid.uuid4())
        orch.get_run = MagicMock(return_value=run)

        result = await server.call_tool(
            "verdity_review", {"owner": "acme", "repo": "api", "pr_number": 5}
        )

    assert result["routing"]["posted"] == 1
    assert result["routing"]["dismissed"] == 1
    gh.create_check_run.assert_not_called()


# ── Guard: no ungated post path can be added silently ─────────────────

_POST_METHODS = {
    "create_check_run",
    "update_check_run",
    "post_pr_comment",
    "post_pr_review",
    "post_inline_comment",
}


def test_every_pr_post_call_site_is_gated():
    """Any function in src/ that calls a PR-posting method must also call gate_findings."""
    src = Path(__file__).resolve().parent.parent / "src" / "verdity"
    offenders = []
    for path in src.rglob("*.py"):
        if path.name == "github_client.py":  # defines the methods
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            called = {
                n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                for n in ast.walk(fn)
                if isinstance(n, ast.Call)
            }
            if called & _POST_METHODS and "gate_findings" not in called:
                offenders.append(f"{path.relative_to(src)}::{fn.name}")
    assert offenders == [], f"ungated PR post paths: {offenders}"


# ── CodingAgent fix PRs go through the same gate ──────────────────────


@pytest.mark.asyncio
async def test_coding_agent_refuses_to_post_a_held_back_finding():
    from verdity.coding_agent import CodingAgent

    # Security pattern the agent can fix, but low enough to need a human:
    # 0.2*0.7 + 0.3 + 0.15 = 0.59 → auto_dismiss; nothing may be posted.
    weak = _finding(severity=Severity.LOW, confidence=0.2, summary="hardcoded password detected")

    with patch("verdity.github_client.GitHubClient") as gh_cls:
        result = await CodingAgent().apply_fix_and_open_pr(
            finding=weak, diff="", owner="acme", repo="api"
        )

    assert result.success is False
    assert "Held back by the posting gate (auto_dismiss)" in result.error
    gh_cls.assert_not_called()


def test_cli_text_output_shows_what_was_held_back(capsys):
    from verdity.cli.review import _print_text_result

    _print_text_result(
        {
            "pr_number": 5,
            "total_findings": 3,
            "routing": {"posted": 1, "queued": 1, "dismissed": 1, "blocking": 0},
            "findings": [],
        }
    )

    out = capsys.readouterr().out
    assert "Posted: 1 · Held for review: 1 · Dismissed: 1 · Blocking: 0" in out
