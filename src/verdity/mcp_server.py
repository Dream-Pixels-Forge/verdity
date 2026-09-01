"""
Verdity MCP Server — Expose specialist agents as MCP tools.

This module implements the Model Context Protocol (MCP) server
for Verdity's specialist agents. Any MCP-compatible client
(Claude Desktop, Cursor, VS Code) can invoke Verdity's
security, quality, testing, and documentation review.

MCP Protocol: https://modelcontextprotocol.io/
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import InspectorConfig
from .model_fallback import MultiModelFallback
from .orchestrator import Orchestrator
from .schemas import (
    ConcernType,
    Finding,
    ReviewPolicy,
    Severity,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentInput:
    """Input for a specialist agent (MCP interface)."""
    diff: str
    context: str = ""
    file_path: str = ""
    language: str = ""
    diff_files: List[Dict[str, Any]] = field(default_factory=list)


def _diff_to_files(diff: str, file_path: str = "") -> List[Dict[str, Any]]:
    """Convert a unified diff string to diff_files format."""
    if not diff:
        return []
    if file_path:
        return [{"path": file_path, "content": diff, "additions": diff, "deletions": ""}]
    return [{"path": "unknown", "content": diff, "additions": diff, "deletions": ""}]

logger = logging.getLogger(__name__)


class MCPServer:
    """MCP server exposing Verdity's specialist agents as tools."""

    PROTOCOL_VERSION = "2024-11-05"
    SERVER_INFO = {
        "name": "verdity",
        "version": "0.3.0",
    }

    def __init__(
        self,
        config: Optional[InspectorConfig] = None,
        multi_model: Optional[MultiModelFallback] = None,
    ) -> None:
        self.config = config or InspectorConfig()
        self.multi_model = multi_model or MultiModelFallback()
        self._orchestrator: Optional[Orchestrator] = None
        self._tools: List[Dict[str, Any]] = self._build_tool_definitions()

    def _build_tool_definitions(self) -> List[Dict[str, Any]]:
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
        self._orchestrator = Orchestrator(config=self.config)
        await self._orchestrator.initialize()
        logger.info("MCP server initialized with %d tools", len(self._tools))

    async def shutdown(self) -> None:
        """Shutdown the MCP server."""
        if self._orchestrator:
            await self._orchestrator.shutdown()
        logger.info("MCP server shutdown")

    def get_tools(self) -> List[Dict[str, Any]]:
        """Return list of available MCP tools."""
        return self._tools

    def get_server_info(self) -> Dict[str, Any]:
        """Return MCP server info."""
        return {
            **self.SERVER_INFO,
            "protocolVersion": self.PROTOCOL_VERSION,
            "tools": self._tools,
        }

    async def call_tool(
        self, name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Call an MCP tool by name with arguments."""
        tool_names = [t["name"] for t in self._tools]
        if name not in tool_names:
            return {
                "error": f"Unknown tool: {name}. Available: {tool_names}",
            }

        try:
            if name == "review_security":
                return await self._review_security(arguments)
            elif name == "review_quality":
                return await self._review_quality(arguments)
            elif name == "review_testing":
                return await self._review_testing(arguments)
            elif name == "review_documentation":
                return await self._review_documentation(arguments)
            elif name == "review_full":
                return await self._review_full(arguments)
            elif name == "generate_fix":
                return await self._generate_fix(arguments)
            elif name == "apply_fix":
                return await self._apply_fix(arguments)
            elif name == "get_review_rules":
                return await self._get_review_rules(arguments)
            else:
                return {"error": f"Tool not implemented: {name}"}
        except Exception as e:
            logger.exception("Error calling tool %s", name)
            return {"error": str(e), "tool": name}

    async def _review_security(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Run security review."""
        from .agents.security import SecurityAgent
        from .semantic_index import SemanticIndex
        from .token_economics import TokenEconomicsService
        from .audit_store import AuditStore
        from .schemas import SpecialistContext

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
                    "severity": f.severity.value if hasattr(f.severity, 'value') else str(f.severity),
                    "confidence": f.confidence,
                }
                for i, f in enumerate(result.findings)
            ]
            return {"findings": findings, "summary": result.summary, "agent": "security"}
        except Exception as e:
            return {"findings": [], "summary": str(e), "agent": "security", "error": str(e)}

    async def _review_quality(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Run code quality review."""
        from .agents.code_quality import CodeQualityAgent
        from .semantic_index import SemanticIndex
        from .token_economics import TokenEconomicsService
        from .audit_store import AuditStore
        from .schemas import SpecialistContext

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
                    "severity": f.severity.value if hasattr(f.severity, 'value') else str(f.severity),
                    "confidence": f.confidence,
                }
                for i, f in enumerate(result.findings)
            ]
            return {"findings": findings, "summary": result.summary, "agent": "quality"}
        except Exception as e:
            return {"findings": [], "summary": str(e), "agent": "quality", "error": str(e)}

    async def _review_testing(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Run testing review."""
        from .agents.testing import TestingAgent
        from .semantic_index import SemanticIndex
        from .token_economics import TokenEconomicsService
        from .audit_store import AuditStore
        from .schemas import SpecialistContext

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
                    "severity": f.severity.value if hasattr(f.severity, 'value') else str(f.severity),
                    "confidence": f.confidence,
                }
                for i, f in enumerate(result.findings)
            ]
            return {"findings": findings, "summary": result.summary, "agent": "testing"}
        except Exception as e:
            return {"findings": [], "summary": str(e), "agent": "testing", "error": str(e)}

    async def _review_documentation(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Run documentation review."""
        from .agents.documentation import DocumentationAgent
        from .semantic_index import SemanticIndex
        from .token_economics import TokenEconomicsService
        from .audit_store import AuditStore
        from .schemas import SpecialistContext

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
                    "severity": f.severity.value if hasattr(f.severity, 'value') else str(f.severity),
                    "confidence": f.confidence,
                }
                for i, f in enumerate(result.findings)
            ]
            return {"findings": findings, "summary": result.summary, "agent": "documentation"}
        except Exception as e:
            return {"findings": [], "summary": str(e), "agent": "documentation", "error": str(e)}

    async def _review_full(self, args: Dict[str, Any]) -> Dict[str, Any]:
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
            result = await self._orchestrator.review(ctx)
            findings = [
                {
                    "rule_id": f"full-{i}",
                    "message": f.summary,
                    "file_path": f.file,
                    "line": f.line_start,
                    "severity": f.severity.value if hasattr(f.severity, 'value') else str(f.severity),
                    "confidence": f.confidence,
                }
                for i, f in enumerate(result.findings)
            ]
            return {"findings": findings, "summary": result.summary, "agent": "full"}
        except Exception as e:
            return {"findings": [], "summary": str(e), "agent": "full", "error": str(e)}

    async def _generate_fix(self, args: Dict[str, Any]) -> Dict[str, Any]:
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

    async def _apply_fix(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a fix to a branch."""
        from .github_client import GitHubClient

        fix_patch = args["fix_patch"]
        file_path = args["file_path"]
        branch = args.get("branch", "main")
        commit_message = args.get(
            "commit_message", "fix: apply automated fix from Verdity"
        )

        client = GitHubClient()
        result = await client.apply_fix(
            file_path=file_path,
            patch=fix_patch,
            branch=branch,
            commit_message=commit_message,
        )

        return result

    async def _get_review_rules(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get custom review rules for a repository."""
        from .review_rules import ReviewRules

        repo_path = args["repo_path"]
        file_path = args.get("file_path", "")

        rules = ReviewRules(repo_path)
        return rules.get_rules(file_path)


def create_mcp_server(
    config: Optional[InspectorConfig] = None,
    multi_model: Optional[MultiModelFallback] = None,
) -> MCPServer:
    """Factory function to create an MCP server instance."""
    return MCPServer(config=config, multi_model=multi_model)
