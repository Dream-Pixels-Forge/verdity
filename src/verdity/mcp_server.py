"""
Verdity MCP Server — Expose specialist agents as MCP tools.

This module implements the Model Context Protocol (MCP) server
for Verdity's specialist agents. Any MCP-compatible client
(Claude Desktop, Cursor, VS Code) can invoke Verdity's
security, quality, testing, and documentation review.

MCP Protocol: https://modelcontextprotocol.io/
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .config import InspectorConfig
from .model_fallback import MultiModelFallback
from .orchestrator import Orchestrator
from .schemas import (
    ReviewPolicy,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentInput:
    """Input for a specialist agent (MCP interface)."""

    diff: str
    context: str = ""
    file_path: str = ""
    language: str = ""
    diff_files: list[dict[str, Any]] = field(default_factory=list)


def _diff_to_files(diff: str, file_path: str = "") -> list[dict[str, Any]]:
    """Convert a unified diff string to diff_files format."""
    if not diff:
        return []
    if file_path:
        return [{"path": file_path, "content": diff, "additions": diff, "deletions": ""}]
    return [{"path": "unknown", "content": diff, "additions": diff, "deletions": ""}]


def _create_finding_proxy(finding_data: dict) -> object:
    """Create a finding proxy object from dict data for evaluation."""
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


logger = logging.getLogger(__name__)


class MCPServer:
    async def _run_orchestrator(
        self,
        *,
        owner: str,
        repo: str,
        pr_number: int,
        head_sha: str = "",
        base_sha: str = "",
        diff: str = "",
        file_path: str = "",
        diff_files: list[dict[str, Any]] | None = None,
        tier: str = "balanced",
    ) -> dict[str, Any]:
        """Drive a review through Orchestrator.process_event().

        Orchestrator has no ``review()``; ``process_event(envelope)`` is the real
        entry point and returns a review_run_id whose run carries
        ``specialist_results``. This builds the VerdityEvent the orchestrator
        expects and flattens every specialist's findings into one list.
        """
        import uuid as _uuid

        from .schemas._models import (
            PullRequestRef,
            QueueEnvelope,
            RepoRef,
            TriggerType,
            VerdityEvent,
        )

        event = VerdityEvent(
            delivery_id=str(_uuid.uuid4()),
            trigger_type=TriggerType.PR_SYNCHRONIZE,
            repo=RepoRef(owner=owner, name=repo, id=0),
            pull_request=PullRequestRef(
                number=pr_number,
                head_sha=head_sha or "unknown",
                base_sha=base_sha or "unknown",
            ),
        )

        # Prefer caller-supplied entries (built from the GitHub API per-file
        # patches); otherwise derive them from a raw unified diff string.
        if diff_files is None:
            diff_files = _diff_to_files(diff, file_path) if diff else []

        run_id = await self._orchestrator.process_event(
            QueueEnvelope(
                event=event,
                diff_files=diff_files,
                tier=tier,
            )
        )

        run = self._orchestrator.get_run(run_id)
        findings: list[dict[str, Any]] = []
        if run is not None:
            for specialist, response in (run.specialist_results or {}).items():
                for i, f in enumerate(getattr(response, "findings", []) or []):
                    findings.append(
                        {
                            "rule_id": f"{specialist}-{i}",
                            "message": f.summary,
                            "file_path": f.file,
                            "line": f.line_start,
                            "severity": getattr(
                                getattr(f, "severity", "info"),
                                "value",
                                str(getattr(f, "severity", "info")),
                            ),
                            "confidence": f.confidence,
                        }
                    )

        return {
            "review_run_id": str(run_id),
            "findings": findings,
            "status": getattr(run, "status", None),
            "summary": (
                f"{len(findings)} finding(s) across "
                f"{len(run.specialist_results or {}) if run else 0} specialist(s)"
            ),
        }

    """MCP server exposing Verdity's specialist agents as tools."""

    PROTOCOL_VERSION: ClassVar[str] = "2024-11-05"
    SERVER_INFO: ClassVar[dict[str, str]] = {
        "name": "verdity",
        "version": "0.4.17",
    }

    def __init__(
        self,
        config: InspectorConfig | None = None,
        multi_model: MultiModelFallback | None = None,
    ) -> None:
        self.config = config or InspectorConfig()
        self.multi_model = multi_model or MultiModelFallback()
        self._orchestrator: Orchestrator | None = None
        self._tools: list[dict[str, Any]] = self._build_tool_definitions()

    def _build_tool_definitions(self) -> list[dict[str, Any]]:
        """Build MCP tool definitions for each specialist agent."""
        return [
            {
                "name": "review_security",
                "description": "Run security analysis on code diff. Detects vulnerabilities, secrets, hardcoded credentials, insecure configurations.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "diff": {
                            "type": "string",
                            "description": "The code diff to analyze (unified diff format)",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional context about the PR (title, description, changed files)",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file being reviewed",
                        },
                        "language": {
                            "type": "string",
                            "description": "Programming language (auto-detected if not provided)",
                        },
                    },
                    "required": ["diff"],
                },
            },
            {
                "name": "review_quality",
                "description": "Run code quality analysis on code diff. Detects complexity issues, anti-patterns, style violations, maintainability concerns.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "diff": {
                            "type": "string",
                            "description": "The code diff to analyze (unified diff format)",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional context about the PR",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file being reviewed",
                        },
                        "language": {
                            "type": "string",
                            "description": "Programming language",
                        },
                    },
                    "required": ["diff"],
                },
            },
            {
                "name": "review_testing",
                "description": "Run test coverage analysis on code diff. Detects missing tests, inadequate coverage, test quality issues.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "diff": {
                            "type": "string",
                            "description": "The code diff to analyze (unified diff format)",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional context about the PR",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file being reviewed",
                        },
                        "language": {
                            "type": "string",
                            "description": "Programming language",
                        },
                    },
                    "required": ["diff"],
                },
            },
            {
                "name": "review_documentation",
                "description": "Run documentation analysis on code diff. Detects missing docstrings, outdated comments, API documentation gaps.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "diff": {
                            "type": "string",
                            "description": "The code diff to analyze (unified diff format)",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional context about the PR",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file being reviewed",
                        },
                        "language": {
                            "type": "string",
                            "description": "Programming language",
                        },
                    },
                    "required": ["diff"],
                },
            },
            {
                "name": "review_full",
                "description": "Run full code review with all specialist agents (security, quality, testing, documentation). Returns aggregated findings with severity scores.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "diff": {
                            "type": "string",
                            "description": "The code diff to analyze (unified diff format)",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional context about the PR",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file being reviewed",
                        },
                        "language": {
                            "type": "string",
                            "description": "Programming language",
                        },
                        "full_context": {
                            "type": "boolean",
                            "description": "Use full-codebase context (requires indexed repo)",
                            "default": False,
                        },
                    },
                    "required": ["diff"],
                },
            },
            # NEW: High-level PR review tools
            {
                "name": "verdity_review",
                "description": "Trigger a full Verdity PR review on a GitHub pull request. Fetches the PR diff and runs all specialist agents.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "owner": {
                            "type": "string",
                            "description": "Repository owner (user or org)",
                        },
                        "repo": {
                            "type": "string",
                            "description": "Repository name",
                        },
                        "pr_number": {
                            "type": "integer",
                            "description": "Pull request number",
                        },
                        "tier": {
                            "type": "string",
                            "enum": ["lite", "balanced", "deep"],
                            "description": "Review tier: lite (fast), balanced (default), deep (full context)",
                            "default": "balanced",
                        },
                        "post_to_github": {
                            "type": "boolean",
                            "description": "Post results as GitHub PR review comments",
                            "default": False,
                        },
                    },
                    "required": ["owner", "repo", "pr_number"],
                },
            },
            {
                "name": "verdity_enforce",
                "description": "Test a finding against Verdity's enforcement engine. Returns the enforcement action (ALLOW/BLOCK/REQUIRE_APPROVAL/ESCALATE).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "finding": {
                            "type": "object",
                            "description": "The finding to test against enforcement rules",
                            "properties": {
                                "rule_id": {"type": "string"},
                                "message": {"type": "string"},
                                "file_path": {"type": "string"},
                                "file": {"type": "string"},
                                "line": {"type": "integer"},
                                "line_start": {"type": "integer"},
                                "line_end": {"type": "integer"},
                                "severity": {
                                    "type": "string",
                                    "enum": ["critical", "high", "medium", "low", "info"],
                                },
                                "confidence": {"type": "number"},
                                "concern": {
                                    "type": "string",
                                    "enum": [
                                        "security",
                                        "code_quality",
                                        "testing",
                                        "documentation",
                                        "performance",
                                        "dependencies",
                                    ],
                                },
                                "summary": {"type": "string"},
                                "explanation": {"type": "string"},
                            },
                            "required": [
                                "file_path",
                                "line_start",
                                "severity",
                                "confidence",
                                "concern",
                            ],
                        },
                        "rules_file": {
                            "type": "string",
                            "description": "Optional path to custom rules YAML file (uses .verdity/rules.yml if not specified)",
                        },
                        "variables": {
                            "type": "object",
                            "description": "Additional context variables for rule evaluation",
                        },
                    },
                    "required": ["finding"],
                },
            },
            {
                "name": "verdity_rules_list",
                "description": "List all enforcement rules from a repository's .verdity/rules.yml file.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_path": {
                            "type": "string",
                            "description": "Path to the repository root",
                        },
                    },
                    "required": ["repo_path"],
                },
            },
            {
                "name": "verdity_review_status",
                "description": "Check the status of a Verdity review run by review_run_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "review_run_id": {
                            "type": "string",
                            "description": "The review run UUID to check",
                        },
                    },
                    "required": ["review_run_id"],
                },
            },
            {
                "name": "generate_fix",
                "description": "Generate a fix for a specific finding. Returns the fix as a code patch.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "finding": {
                            "type": "object",
                            "description": "The finding to fix (from review results)",
                            "properties": {
                                "rule_id": {"type": "string"},
                                "message": {"type": "string"},
                                "file_path": {"type": "string"},
                                "line": {"type": "integer"},
                                "severity": {"type": "string"},
                            },
                            "required": ["rule_id", "message", "file_path"],
                        },
                        "diff": {
                            "type": "string",
                            "description": "The original code diff",
                        },
                        "context": {
                            "type": "string",
                            "description": "Additional context about the codebase",
                        },
                    },
                    "required": ["finding", "diff"],
                },
            },
            {
                "name": "apply_fix",
                "description": "Apply a generated fix to a branch and create a commit. Returns the commit SHA.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fix_patch": {
                            "type": "string",
                            "description": "The fix patch to apply",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "The file to apply the fix to",
                        },
                        "branch": {
                            "type": "string",
                            "description": "Target branch (default: current branch)",
                        },
                        "commit_message": {
                            "type": "string",
                            "description": "Commit message for the fix",
                        },
                    },
                    "required": ["fix_patch", "file_path"],
                },
            },
            {
                "name": "get_review_rules",
                "description": "Get custom review rules for a repository. Returns rules from .verdity/rules.yml",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo_path": {
                            "type": "string",
                            "description": "Path to the repository root",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Optional specific file path to get rules for",
                        },
                    },
                    "required": ["repo_path"],
                },
            },
        ]

    async def initialize(self) -> None:
        """Initialize the MCP server and orchestrator."""
        # Orchestrator takes its collaborators explicitly (queue, semantic
        # index, token economics, audit store) — it does not accept a config
        # object. Constructing it with `config=` raised TypeError, and there is
        # no async initialize()/shutdown() on Orchestrator. Follow the same
        # wiring worker.py uses so both entry points behave alike.
        from .audit_store import AuditStore
        from .event_queue import EventQueue
        from .semantic_index import SemanticIndex
        from .token_economics import TokenEconomicsService

        self._queue = EventQueue()
        self._index = SemanticIndex()
        self._token_economics = TokenEconomicsService()
        self._audit = AuditStore()

        # These stores refuse use until connect() is awaited; Orchestrator hits
        # them on the first process_event().
        for collaborator in (
            self._queue,
            self._index,
            self._token_economics,
            self._audit,
        ):
            connect = getattr(collaborator, "connect", None)
            if connect is not None:
                await connect()

        self._orchestrator = Orchestrator(
            queue=self._queue,
            semantic_index=self._index,
            token_economics=self._token_economics,
            audit_store=self._audit,
        )

        # Specialists must be registered or process_event() dispatches to
        # nothing and every review comes back empty.
        from .agents import (
            CodeQualityAgent,
            DocumentationAgent,
            SecurityAgent,
            TestingAgent,
        )

        fallback = self.multi_model
        self._orchestrator.register_specialist("security", SecurityAgent(fallback=fallback).run)
        self._orchestrator.register_specialist(
            "code_quality", CodeQualityAgent(fallback=fallback).run
        )
        self._orchestrator.register_specialist("testing", TestingAgent(fallback=fallback).run)
        self._orchestrator.register_specialist(
            "documentation", DocumentationAgent(fallback=fallback).run
        )

        logger.info("MCP server initialized with %d tools", len(self._tools))

    async def shutdown(self) -> None:
        """Shutdown the MCP server."""
        # Orchestrator exposes no shutdown(); close the collaborators we opened.
        # Each is optional so shutdown() is safe before initialize() ran.
        for name in ("_queue", "_index", "_token_economics", "_audit"):
            closer = getattr(getattr(self, name, None), "close", None)
            if closer is not None:
                await closer()
        logger.info("MCP server shutdown")

    def get_tools(self) -> list[dict[str, Any]]:
        """Return list of available MCP tools."""
        return self._tools

    def get_server_info(self) -> dict[str, Any]:
        """Return MCP server info."""
        return {
            **self.SERVER_INFO,
            "protocolVersion": self.PROTOCOL_VERSION,
            "tools": self._tools,
        }

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call an MCP tool by name with arguments."""
        tool_names = [t["name"] for t in self._tools]
        if name not in tool_names:
            return {
                "error": f"Unknown tool: {name}. Available: {tool_names}",
            }

        try:
            if name == "review_security":
                return await self._review_security(arguments)
            if name == "review_quality":
                return await self._review_quality(arguments)
            if name == "review_testing":
                return await self._review_testing(arguments)
            if name == "review_documentation":
                return await self._review_documentation(arguments)
            if name == "review_full":
                return await self._review_full(arguments)
            # NEW tools
            if name == "verdity_review":
                return await self._verdity_review(arguments)
            if name == "verdity_enforce":
                return await self._verdity_enforce(arguments)
            if name == "verdity_rules_list":
                return await self._verdity_rules_list(arguments)
            if name == "verdity_review_status":
                return await self._verdity_review_status(arguments)
            if name == "generate_fix":
                return await self._generate_fix(arguments)
            if name == "apply_fix":
                return await self._apply_fix(arguments)
            if name == "get_review_rules":
                return await self._get_review_rules(arguments)
            return {"error": f"Tool not implemented: {name}"}
        except Exception as e:
            logger.exception("Error calling tool %s", name)
            return {"error": str(e), "tool": name}

    async def _review_security(self, args: dict[str, Any]) -> dict[str, Any]:
        """Run security review."""
        from .agents.security import SecurityAgent
        from .audit_store import AuditStore
        from .schemas import SpecialistContext
        from .semantic_index import SemanticIndex
        from .token_economics import TokenEconomicsService

        diff = args.get("diff", "")
        file_path = args.get("file_path", "")

        ctx = SpecialistContext(
            review_run_id=uuid.uuid4(),
            repo_owner="mcp",
            repo_name="client",
            base_sha="",
            head_sha="",
            diff_files=_diff_to_files(diff, file_path),
            policy=ReviewPolicy(),
        )

        agent = SecurityAgent(fallback=self.multi_model)
        index = SemanticIndex()
        economics = TokenEconomicsService()
        audit = AuditStore()

        try:
            result = await agent.run(ctx, index, economics, audit)
            findings = [
                {
                    "rule_id": f"security-{i}",
                    "message": f.summary,
                    "file_path": f.file,
                    "line": f.line_start,
                    "severity": f.severity.value
                    if hasattr(f.severity, "value")
                    else str(f.severity),
                    "confidence": f.confidence,
                }
                for i, f in enumerate(result.findings)
            ]
            return {"findings": findings, "summary": result.summary, "agent": "security"}
        except Exception as e:
            return {"findings": [], "summary": str(e), "agent": "security", "error": str(e)}

    async def _review_quality(self, args: dict[str, Any]) -> dict[str, Any]:
        """Run code quality review."""
        from .agents.code_quality import CodeQualityAgent
        from .audit_store import AuditStore
        from .schemas import SpecialistContext
        from .semantic_index import SemanticIndex
        from .token_economics import TokenEconomicsService

        diff = args.get("diff", "")
        file_path = args.get("file_path", "")

        ctx = SpecialistContext(
            review_run_id=uuid.uuid4(),
            repo_owner="mcp",
            repo_name="client",
            base_sha="",
            head_sha="",
            diff_files=_diff_to_files(diff, file_path),
            policy=ReviewPolicy(),
        )

        agent = CodeQualityAgent(fallback=self.multi_model)
        index = SemanticIndex()
        economics = TokenEconomicsService()
        audit = AuditStore()

        try:
            result = await agent.run(ctx, index, economics, audit)
            findings = [
                {
                    "rule_id": f"quality-{i}",
                    "message": f.summary,
                    "file_path": f.file,
                    "line": f.line_start,
                    "severity": f.severity.value
                    if hasattr(f.severity, "value")
                    else str(f.severity),
                    "confidence": f.confidence,
                }
                for i, f in enumerate(result.findings)
            ]
            return {"findings": findings, "summary": result.summary, "agent": "quality"}
        except Exception as e:
            return {"findings": [], "summary": str(e), "agent": "quality", "error": str(e)}

    async def _review_testing(self, args: dict[str, Any]) -> dict[str, Any]:
        """Run testing review."""
        from .agents.testing import TestingAgent
        from .audit_store import AuditStore
        from .schemas import SpecialistContext
        from .semantic_index import SemanticIndex
        from .token_economics import TokenEconomicsService

        diff = args.get("diff", "")
        file_path = args.get("file_path", "")

        ctx = SpecialistContext(
            review_run_id=uuid.uuid4(),
            repo_owner="mcp",
            repo_name="client",
            base_sha="",
            head_sha="",
            diff_files=_diff_to_files(diff, file_path),
            policy=ReviewPolicy(),
        )

        agent = TestingAgent(fallback=self.multi_model)
        index = SemanticIndex()
        economics = TokenEconomicsService()
        audit = AuditStore()

        try:
            result = await agent.run(ctx, index, economics, audit)
            findings = [
                {
                    "rule_id": f"testing-{i}",
                    "message": f.summary,
                    "file_path": f.file,
                    "line": f.line_start,
                    "severity": f.severity.value
                    if hasattr(f.severity, "value")
                    else str(f.severity),
                    "confidence": f.confidence,
                }
                for i, f in enumerate(result.findings)
            ]
            return {"findings": findings, "summary": result.summary, "agent": "testing"}
        except Exception as e:
            return {"findings": [], "summary": str(e), "agent": "testing", "error": str(e)}

    async def _review_documentation(self, args: dict[str, Any]) -> dict[str, Any]:
        """Run documentation review."""
        from .agents.documentation import DocumentationAgent
        from .audit_store import AuditStore
        from .schemas import SpecialistContext
        from .semantic_index import SemanticIndex
        from .token_economics import TokenEconomicsService

        diff = args.get("diff", "")
        file_path = args.get("file_path", "")

        ctx = SpecialistContext(
            review_run_id=uuid.uuid4(),
            repo_owner="mcp",
            repo_name="client",
            base_sha="",
            head_sha="",
            diff_files=_diff_to_files(diff, file_path),
            policy=ReviewPolicy(),
        )

        agent = DocumentationAgent(fallback=self.multi_model)
        index = SemanticIndex()
        economics = TokenEconomicsService()
        audit = AuditStore()

        try:
            result = await agent.run(ctx, index, economics, audit)
            findings = [
                {
                    "rule_id": f"docs-{i}",
                    "message": f.summary,
                    "file_path": f.file,
                    "line": f.line_start,
                    "severity": f.severity.value
                    if hasattr(f.severity, "value")
                    else str(f.severity),
                    "confidence": f.confidence,
                }
                for i, f in enumerate(result.findings)
            ]
            return {"findings": findings, "summary": result.summary, "agent": "documentation"}
        except Exception as e:
            return {"findings": [], "summary": str(e), "agent": "documentation", "error": str(e)}

    async def _review_full(self, args: dict[str, Any]) -> dict[str, Any]:
        """Run full review with all agents."""
        if not self._orchestrator:
            await self.initialize()

        diff = args.get("diff", "")
        file_path = args.get("file_path", "")

        from .schemas import SpecialistContext

        ctx = SpecialistContext(
            review_run_id=uuid.uuid4(),
            repo_owner="mcp",
            repo_name="client",
            base_sha="",
            head_sha="",
            diff_files=_diff_to_files(diff, file_path),
            policy=ReviewPolicy(),
        )

        try:
            result = await self._run_orchestrator(
                owner="mcp",
                repo="client",
                pr_number=0,
                diff=diff,
                file_path=file_path,
                tier="balanced",
            )
            return {
                "findings": result["findings"],
                "summary": result["summary"],
                "agent": "full",
            }
        except Exception as e:
            return {"findings": [], "summary": str(e), "agent": "full", "error": str(e)}

    # NEW: High-level PR review tools

    async def _verdity_review(self, args: dict[str, Any]) -> dict[str, Any]:
        """Trigger a full Verdity PR review on a GitHub PR."""
        from .github_client import GitHubClient
        from .schemas import SpecialistContext, ReviewPolicy
        from verdity.config import get_settings

        owner = args["owner"]
        repo = args["repo"]
        pr_number = args["pr_number"]
        tier = args.get("tier", "balanced")
        post_to_github = args.get("post_to_github", False)

        if not self._orchestrator:
            await self.initialize()

        # Get GitHub client from settings
        settings = get_settings()
        client = GitHubClient(
            app_id=settings.github_app_id,
            private_key_pem=settings.github_app_private_key.get_secret_value(),
            installation_id=settings.github_app_installation_id,
            token=settings.github_token.get_secret_value() if settings.github_token else None,
        )

        try:
            # Fetch PR diff
            pr_diff = await client.get_pr_diff(owner, repo, pr_number)
            if not pr_diff:
                return {"error": "Failed to fetch PR diff", "pr_number": pr_number}

            # Convert diff to diff_files format
            diff_files = []
            for file_change in pr_diff.get("files", []):
                diff_files.append(
                    {
                        "path": file_change.get("filename", "unknown"),
                        "content": file_change.get("patch", ""),
                        "additions": file_change.get("additions", 0),
                        "deletions": file_change.get("deletions", 0),
                    }
                )

            # Determine policy based on tier
            policy = ReviewPolicy(
                tier=tier,
                timeout_seconds={"lite": 30, "balanced": 120, "deep": 300}[tier],
                budget_tokens={"lite": 5000, "balanced": 40000, "deep": 200000}[tier],
            )

            ctx = SpecialistContext(
                review_run_id=uuid.uuid4(),
                repo_owner=owner,
                repo_name=repo,
                base_sha=pr_diff.get("base_sha", ""),
                head_sha=pr_diff.get("head_sha", ""),
                diff_files=diff_files,
                policy=policy,
            )

            # Run review through the orchestrator's real entry point.
            result = await self._run_orchestrator(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                head_sha=pr_diff.get("head_sha", ""),
                base_sha=pr_diff.get("base_sha", ""),
                diff_files=diff_files,
                tier=tier,
            )

            findings = result["findings"]

            response = {
                "review_run_id": result["review_run_id"],
                "pr_number": pr_number,
                "tier": tier,
                "findings": findings,
                "summary": result["summary"],
                "total_findings": len(findings),
            }

            # Optionally post to GitHub
            if post_to_github:
                from verdity.github_client import create_check_output
                from verdity.schemas import Finding

                postable = [
                    Finding(
                        summary=f.get("message", ""),
                        file=f.get("file_path", ""),
                        line_start=f.get("line", 1),
                    )
                    for f in findings
                ]
                check_output = create_check_output(postable)
                check_result = await client.post_check_run(
                    owner=owner,
                    repo=repo,
                    head_sha=pr_diff.get("head_sha", ""),
                    name="Verdity Code Review",
                    output=check_output,
                )
                response["github_check"] = check_result

            return response

        except Exception as e:
            logger.exception("Error in verdity_review")
            return {"error": str(e), "pr_number": pr_number}

    async def _verdity_enforce(self, args: dict[str, Any]) -> dict[str, Any]:
        """Test a finding against Verdity's enforcement engine."""
        from .enforcement import EnforcementEngine, GateRule, Action, load_rules_from_yaml

        finding_data = args["finding"]
        rules_file = args.get("rules_file")
        variables = args.get("variables", {})

        try:
            # Load rules
            if rules_file:
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
            else:
                # Try to load from .verdity/rules.yml
                try:
                    rules = load_rules_from_yaml(".verdity/rules.yml")
                except Exception:
                    rules = []

            engine = EnforcementEngine(rules=rules)

            # Create finding proxy
            finding_proxy = _create_finding_proxy(finding_data)

            # Evaluate
            decision = await engine.evaluate_with_context(finding_proxy, variables)

            return {
                "action": decision.action.upper(),
                "blocked": decision.blocked,
                "rule_id": decision.rule_id,
                "message": decision.message,
                "finding": finding_data,
            }
        except Exception as e:
            logger.exception("Error in verdity_enforce")
            return {"error": str(e), "finding": finding_data}

    async def _verdity_rules_list(self, args: dict[str, Any]) -> dict[str, Any]:
        """List all enforcement rules from a repository's .verdity/rules.yml file."""
        from .enforcement import load_rules_from_yaml
        import yaml

        repo_path = args["repo_path"]
        rules_file = f"{repo_path}/.verdity/rules.yml"

        try:
            with open(rules_file) as f:
                data = yaml.safe_load(f)

            rules = data.get("rules", [])
            return {
                "repo_path": repo_path,
                "rules_file": rules_file,
                "rules": rules,
            }
        except FileNotFoundError:
            return {
                "error": "Rules file not found",
                "repo_path": repo_path,
                "rules_file": rules_file,
            }
        except Exception as e:
            return {"error": str(e), "repo_path": repo_path}

    async def _verdity_review_status(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check the status of a Verdity review run."""
        # This would require the orchestrator to expose review run state
        # For now, return a placeholder
        review_run_id = args["review_run_id"]

        return {
            "review_run_id": review_run_id,
            "status": "unknown",
            "message": "Review run status tracking not yet implemented in orchestrator",
        }

    async def _generate_fix(self, args: dict[str, Any]) -> dict[str, Any]:
        """Generate a fix for a finding."""
        from .coding_agent import CodingAgent

        finding = args["finding"]
        diff = args["diff"]
        context = args.get("context", "")

        agent = CodingAgent(multi_model=self.multi_model)
        fix = await agent.generate_fix(finding, diff, context)

        return {
            "fix": fix,
            "finding": finding,
        }

    async def _apply_fix(self, args: dict[str, Any]) -> dict[str, Any]:
        """Apply a fix to a branch."""
        from .github_client import GitHubClient

        fix_patch = args["fix_patch"]
        file_path = args["file_path"]
        branch = args.get("branch", "main")
        commit_message = args.get("commit_message", "fix: apply automated fix from Verdity")

        client = GitHubClient()
        result = await client.apply_fix(
            file_path=file_path,
            patch=fix_patch,
            branch=branch,
            commit_message=commit_message,
        )

        return result

    async def _get_review_rules(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get custom review rules for a repository."""
        from .review_rules import ReviewRules

        repo_path = args["repo_path"]
        file_path = args.get("file_path", "")

        rules = ReviewRules(repo_path)
        return rules.get_rules(file_path)


def create_mcp_server(
    config: InspectorConfig | None = None,
    multi_model: MultiModelFallback | None = None,
) -> MCPServer:
    """Factory function to create an MCP server instance."""
    return MCPServer(config=config, multi_model=multi_model)
