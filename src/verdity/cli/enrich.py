"""
CLI command for LLM-based finding enrichment.

Usage:
    verdity-enrich --run-id <uuid> --mode explain|fix|risk|all --model <name>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from verdity.agents.llm_enricher import EnrichmentMode, LLMEnricherAgent
from verdity.audit_store import AuditStore
from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
from verdity.schemas import ReviewPolicy, SpecialistContext
from verdity.token_economics import TokenEconomicsService


async def enrich_findings_command(args: argparse.Namespace) -> int:
    """Run the enrichment command."""
    # Parse review run ID
    try:
        review_run_id = uuid.UUID(args.run_id)
    except ValueError:
        print(f"Error: Invalid UUID format for --run-id: {args.run_id}", file=sys.stderr)
        return 1

    # Parse mode
    try:
        mode = EnrichmentMode(args.mode)
    except ValueError:
        print(
            f"Error: Invalid mode: {args.mode}. Must be one of: explain, fix, risk, all",
            file=sys.stderr,
        )
        return 1

    # Read findings from file or stdin
    if args.findings_file:
        with open(args.findings_file) as f:
            findings_data = json.load(f)
    else:
        # Read from stdin
        findings_data = json.load(sys.stdin)

    # Read diff from file or stdin
    if args.diff_file:
        with open(args.diff_file) as f:
            diff = f.read()
    else:
        diff = sys.stdin.read()

    if not diff:
        print("Error: diff is required (provide via --diff-file or stdin)", file=sys.stderr)
        return 1

    # Create gateway config
    config = GatewayConfig(
        model=args.model,
        backend=args.backend,
        base_url=args.base_url or "",
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )

    # Create gateway
    gateway = LocalLLMGateway(config=config)

    # Create context
    ctx = SpecialistContext(
        review_run_id=review_run_id,
        repo_owner=args.repo_owner or "cli",
        repo_name=args.repo_name or "enrich",
        base_sha="",
        head_sha="",
        diff_files=[],
        policy=ReviewPolicy(),
    )

    # Parse findings
    from verdity.schemas import ConcernType, EvidenceItem, Finding, Severity

    findings = []
    for f in findings_data:
        concern = ConcernType(f["concern"])
        severity = Severity(f["severity"])
        finding = Finding(
            finding_id=uuid.UUID(f["finding_id"]) if "finding_id" in f else uuid.uuid4(),
            concern=concern,
            severity=severity,
            file=f.get("file", ""),
            line_start=f.get("line_start", 0),
            line_end=f.get("line_end", 0),
            summary=f.get("summary", ""),
            explanation=f.get("explanation", ""),
            suggested_fix_diff=f.get("suggested_fix_diff"),
            confidence=f.get("confidence", 0.5),
            evidence=[EvidenceItem(**e) for e in f.get("evidence", [])],
            agent_version=f.get("agent_version", "cli-enrich"),
            prompt_hash=f.get("prompt_hash", ""),
        )
        findings.append(finding)

    # Run enrichment
    token_economics = TokenEconomicsService()
    audit_store = AuditStore(db_path=args.db_path or ":memory:")
    await audit_store.connect()

    enricher = LLMEnricherAgent(gateway=gateway)
    results = await enricher.enrich_findings(
        findings=findings,
        diff=diff,
        context=ctx,
        mode=mode,
        token_economics=token_economics,
        audit_store=audit_store,
    )

    await audit_store.close()
    await gateway.close()

    # Output results
    output = []
    for r in results:
        item = {
            "finding_id": r.finding_id,
            "mode": r.mode.value,
            "model": r.model,
            "backend": r.backend,
        }
        if r.explanation is not None:
            item["explanation"] = r.explanation
        if r.confidence_adjustment_rationale is not None:
            item["confidence_adjustment_rationale"] = r.confidence_adjustment_rationale
        if r.fix_diff is not None:
            item["fix_diff"] = r.fix_diff
        if r.fix_explanation is not None:
            item["fix_explanation"] = r.fix_explanation
        if r.risk_score is not None:
            item["risk_score"] = r.risk_score
        if r.cwe_ids is not None:
            item["cwe_ids"] = r.cwe_ids
        if r.exploitability is not None:
            item["exploitability"] = r.exploitability
        if r.blast_radius is not None:
            item["blast_radius"] = r.blast_radius
        output.append(item)

    print(json.dumps(output, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enrich findings with LLM-generated explanations, fixes, and risk assessments",
        prog="verdity-enrich",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Review run UUID to associate enrichments with",
    )
    parser.add_argument(
        "--mode",
        choices=["explain", "fix", "risk", "all"],
        default="explain",
        help="Enrichment mode (default: explain)",
    )
    parser.add_argument(
        "--model",
        default="qwen2.5-7b-instruct",
        help="LLM model to use (default: qwen2.5-7b-instruct)",
    )
    parser.add_argument(
        "--backend",
        choices=["ollama", "vllm", "llamacpp", "openai"],
        default="ollama",
        help="LLM backend (default: ollama)",
    )
    parser.add_argument(
        "--base-url",
        help="Base URL for LLM backend (default varies by backend)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (default: 0.0)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Maximum tokens in response (default: 4096)",
    )
    parser.add_argument(
        "--findings-file",
        help="JSON file containing findings to enrich (default: stdin)",
    )
    parser.add_argument(
        "--diff-file",
        help="File containing diff context (default: stdin)",
    )
    parser.add_argument(
        "--repo-owner",
        help="Repository owner for audit trail",
    )
    parser.add_argument(
        "--repo-name",
        help="Repository name for audit trail",
    )
    parser.add_argument(
        "--db-path",
        help="Audit store database path (default: in-memory)",
    )
    parser.add_argument(
        "--output-file",
        help="Write JSON output to file instead of stdout",
    )

    args = parser.parse_args()

    try:
        return asyncio.run(enrich_findings_command(args))
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
