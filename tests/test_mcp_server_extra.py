"""Additional MCP Server tests covering missing branches in mcp_server.py."""

from __future__ import annotations

import tempfile
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdity.mcp_server import MCPServer, _diff_to_files
from verdity.schemas import ConcernType, Finding, Severity, SpecialistResponse


class TestDiffToFiles:
    """Cover both branches of _diff_to_files (empty diff / file_path provided)."""

    def test_empty_diff_returns_empty_list(self):
        assert _diff_to_files("") == []

    def test_diff_with_file_path(self):
        result = _diff_to_files("+ new line", "src/x.py")
        assert len(result) == 1
        assert result[0]["path"] == "src/x.py"

    def test_diff_without_file_path(self):
        result = _diff_to_files("+ new line")
        assert len(result) == 1
        assert result[0]["path"] == "unknown"


class TestMCPLifecycle:
    """initialize() and shutdown() — both branches exercised."""

    @pytest.mark.asyncio
    async def test_initialize_creates_orchestrator(self):
        server = MCPServer()
        with patch("verdity.mcp_server.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.initialize = AsyncMock()
            mock_orch_cls.return_value = mock_orch
            await server.initialize()
            assert server._orchestrator is mock_orch
            mock_orch.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_with_orchestrator(self):
        server = MCPServer()
        mock_orch = MagicMock()
        mock_orch.shutdown = AsyncMock()
        server._orchestrator = mock_orch
        await server.shutdown()
        mock_orch.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_without_orchestrator(self):
        server = MCPServer()
        server._orchestrator = None
        # Must not raise
        await server.shutdown()


class TestCallToolExceptions:
    """Cover the except branches and the _review_full orchestrator-not-yet-initialized path."""

    @staticmethod
    def _real_finding(summary: str = "issue", severity: Severity = Severity.HIGH) -> Finding:
        """A real Finding model — never inject fields the real model lacks."""
        return Finding(
            concern=ConcernType.SECURITY,
            severity=severity,
            file="x.py",
            line_start=1,
            line_end=1,
            summary=summary,
            explanation="e",
            confidence=0.5,
            agent_version="v",
            prompt_hash="h",
        )

    @staticmethod
    def _real_response(findings: list[Finding] | None = None, specialist: str = "security"):
        return SpecialistResponse(
            review_run_id=uuid.uuid4(),
            specialist=specialist,
            status="complete",
            findings=findings or [],
        )

    @pytest.mark.asyncio
    async def test_call_tool_review_security_returns_findings(self):
        """Cover the success path with non-empty findings."""
        server = MCPServer()
        with patch("verdity.agents.security.SecurityAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=self._real_response([self._real_finding()]))
            mock_agent.return_value = mock_instance
            result = await server.call_tool(
                "review_security",
                {"diff": "test", "file_path": "x.py"},
            )
            assert result["findings"][0]["rule_id"] == "security-0"
            assert result["agent"] == "security"
            assert "error" not in result
            assert result["summary"] == "1 finding"

    @pytest.mark.asyncio
    async def test_call_tool_review_quality_returns_findings(self):
        server = MCPServer()
        with patch("verdity.agents.code_quality.CodeQualityAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(
                return_value=self._real_response([self._real_finding()], specialist="quality")
            )
            mock_agent.return_value = mock_instance
            result = await server.call_tool(
                "review_quality",
                {"diff": "test", "file_path": "x.py"},
            )
            assert result["findings"][0]["rule_id"] == "quality-0"

    @pytest.mark.asyncio
    async def test_call_tool_review_testing_returns_findings(self):
        server = MCPServer()
        with patch("verdity.agents.testing.TestingAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(
                return_value=self._real_response([self._real_finding()], specialist="testing")
            )
            mock_agent.return_value = mock_instance
            result = await server.call_tool(
                "review_testing",
                {"diff": "test", "file_path": "x.py"},
            )
            assert result["findings"][0]["rule_id"] == "testing-0"

    @pytest.mark.asyncio
    async def test_call_tool_review_documentation_returns_findings(self):
        server = MCPServer()
        with patch("verdity.agents.documentation.DocumentationAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(
                return_value=self._real_response([self._real_finding()], specialist="documentation")
            )
            mock_agent.return_value = mock_instance
            result = await server.call_tool(
                "review_documentation",
                {"diff": "test", "file_path": "x.py"},
            )
            assert result["findings"][0]["rule_id"] == "docs-0"

    @pytest.mark.asyncio
    async def test_call_tool_review_security_raises(self):
        server = MCPServer()
        with patch("verdity.agents.security.SecurityAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(side_effect=RuntimeError("boom"))
            mock_agent.return_value = mock_instance
            result = await server.call_tool(
                "review_security",
                {"diff": "test", "file_path": "x.py"},
            )
            assert "error" in result
            assert result["status"] == "error"
            assert result["error"]
            assert result["agent"] == "security"

    @pytest.mark.asyncio
    async def test_call_tool_review_quality_raises(self):
        server = MCPServer()
        with patch("verdity.agents.code_quality.CodeQualityAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(side_effect=RuntimeError("boom"))
            mock_agent.return_value = mock_instance
            result = await server.call_tool(
                "review_quality",
                {"diff": "test", "file_path": "x.py"},
            )
            assert "error" in result
            assert result["status"] == "error"
            assert result["error"]
            assert result["agent"] == "quality"

    @pytest.mark.asyncio
    async def test_call_tool_review_testing_raises(self):
        server = MCPServer()
        with patch("verdity.agents.testing.TestingAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(side_effect=RuntimeError("boom"))
            mock_agent.return_value = mock_instance
            result = await server.call_tool(
                "review_testing",
                {"diff": "test", "file_path": "x.py"},
            )
            assert "error" in result
            assert result["status"] == "error"
            assert result["error"]
            assert result["agent"] == "testing"

    @pytest.mark.asyncio
    async def test_call_tool_review_documentation_raises(self):
        server = MCPServer()
        with patch("verdity.agents.documentation.DocumentationAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(side_effect=RuntimeError("boom"))
            mock_agent.return_value = mock_instance
            result = await server.call_tool(
                "review_documentation",
                {"diff": "test", "file_path": "x.py"},
            )
            assert "error" in result
            assert result["status"] == "error"
            assert result["error"]
            assert result["agent"] == "documentation"

    @pytest.mark.asyncio
    async def test_call_tool_review_full_raises(self):
        server = MCPServer()
        with patch("verdity.mcp_server.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.initialize = AsyncMock()
            mock_orch.review = AsyncMock(side_effect=RuntimeError("boom"))
            mock_orch_cls.return_value = mock_orch
            result = await server.call_tool(
                "review_full",
                {"diff": "test", "file_path": "x.py"},
            )
            assert "error" in result
            assert result["status"] == "error"
            assert result["error"]
            assert result["agent"] == "full"

    @pytest.mark.asyncio
    async def test_call_tool_apply_fix(self):
        """Cover _apply_fix branch."""
        server = MCPServer()
        with patch("verdity.github_client.GitHubClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.apply_fix = AsyncMock(return_value={"ok": True})
            mock_client_cls.return_value = mock_instance
            result = await server.call_tool(
                "apply_fix",
                {"fix_patch": "+ x", "file_path": "x.py"},
            )
            assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_call_tool_get_review_rules(self):
        """Cover _get_review_rules branch."""
        server = MCPServer()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await server.call_tool(
                "get_review_rules",
                {"repo_path": tmpdir, "file_path": "x.py"},
            )
            assert "version" in result

    @pytest.mark.asyncio
    async def test_call_tool_top_level_exception(self):
        """Force a top-level exception by passing invalid arguments to a generator."""
        # Use a non-existent tool path that gets past validation but raises elsewhere
        # The cleanest way is to patch one of the helper methods to raise
        server = MCPServer()
        with patch.object(server, "_review_security", side_effect=RuntimeError("kaboom")):
            result = await server.call_tool("review_security", {"diff": "x"})
            assert "error" in result
            assert result["tool"] == "review_security"


class TestReviewFullInitialize:
    """Cover the _review_full branch that calls initialize() if orchestrator is None."""

    @pytest.mark.asyncio
    async def test_review_full_initializes_orchestrator_if_missing(self):
        server = MCPServer()
        assert server._orchestrator is None

        with patch("verdity.mcp_server.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.initialize = AsyncMock()

            # Review returns a real SpecialistResponse with findings
            mock_result = SpecialistResponse(
                review_run_id=uuid.uuid4(),
                specialist="full",
                status="complete",
                findings=[
                    Finding(
                        concern=ConcernType.SECURITY,
                        severity=Severity.HIGH,
                        file="x.py",
                        line_start=1,
                        line_end=1,
                        summary="issue",
                        explanation="e",
                        confidence=0.5,
                        agent_version="v",
                        prompt_hash="h",
                    )
                ],
            )
            mock_orch.review = AsyncMock(return_value=mock_result)

            mock_orch_cls.return_value = mock_orch
            result = await server.call_tool("review_full", {"diff": "x", "file_path": "x.py"})
            mock_orch.initialize.assert_awaited_once()
            assert result["agent"] == "full"
            assert result["findings"][0]["rule_id"] == "full-0"


class TestUnreachableElseBranch:
    """Defensive else branch in call_tool (line 334) — reached only when a tool
    name passes the validation but has no handler. Force by mutating _tools."""

    @pytest.mark.asyncio
    async def test_force_unreachable_else_in_call_tool(self):
        """Inject a tool name that exists in _tools but lacks a handler.

        The current code's tool list maps all 8 names. To exercise the
        defensive else branch, we add a phantom tool to the in-memory list.
        """
        server = MCPServer()
        # Append a tool name that won't match any branch
        server._tools.append({"name": "phantom_tool", "description": "x"})
        result = await server.call_tool("phantom_tool", {})
        assert "error" in result
        assert "Tool not implemented" in result["error"]
