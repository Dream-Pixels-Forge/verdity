"""Tests for MCP Server module."""

import uuid
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
import yaml

import verdity.github_client
import verdity.mcp_server
from verdity.mcp_server import MCPServer, create_mcp_server


def _mock_orchestrator_review(mock_orchestrator, mock_result):
    """Point a mocked Orchestrator at its REAL review entry point.

    Orchestrator exposes process_event(envelope) -> review_run_id and
    get_run(id) -> run with .specialist_results. There is no review().
    Mocking review() made these tests assert against a method that does not
    exist, which is how the unwired MCP path survived.
    """
    import uuid as _uuid

    run = MagicMock()
    run.specialist_results = {"security": mock_result}
    mock_orchestrator.process_event = AsyncMock(return_value=_uuid.uuid4())
    mock_orchestrator.get_run = MagicMock(return_value=run)
    return mock_orchestrator


class TestMCPServer:
    def test_init_defaults(self):
        server = MCPServer()
        assert server.PROTOCOL_VERSION == "2024-11-05"
        assert server.SERVER_INFO["name"] == "verdity"
        assert server.SERVER_INFO["version"] == "0.4.17"
        assert len(server._tools) == 12

    def test_get_tools(self):
        server = MCPServer()
        tools = server.get_tools()
        assert len(tools) == 12
        tool_names = [t["name"] for t in tools]
        # Original tools
        assert "review_security" in tool_names
        assert "review_quality" in tool_names
        assert "review_testing" in tool_names
        assert "review_documentation" in tool_names
        assert "review_full" in tool_names
        assert "generate_fix" in tool_names
        assert "apply_fix" in tool_names
        assert "get_review_rules" in tool_names
        # New tools
        assert "verdity_review" in tool_names
        assert "verdity_enforce" in tool_names
        assert "verdity_rules_list" in tool_names
        assert "verdity_review_status" in tool_names

    def test_get_server_info(self):
        server = MCPServer()
        info = server.get_server_info()
        assert info["name"] == "verdity"
        assert info["version"] == "0.4.17"
        assert info["protocolVersion"] == "2024-11-05"
        assert "tools" in info

    @pytest.mark.asyncio
    async def test_call_tool_unknown(self):
        server = MCPServer()
        result = await server.call_tool("unknown_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_call_tool_review_security(self):
        server = MCPServer()
        with patch("verdity.agents.security.SecurityAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            mock_instance.run = AsyncMock(return_value=mock_result)
            mock_agent.return_value = mock_instance

            result = await server.call_tool(
                "review_security", {"diff": "test diff", "file_path": "test.py"}
            )
            assert "findings" in result
            assert result["agent"] == "security"

    @pytest.mark.asyncio
    async def test_call_tool_review_quality(self):
        server = MCPServer()
        with patch("verdity.agents.code_quality.CodeQualityAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            mock_instance.run = AsyncMock(return_value=mock_result)
            mock_agent.return_value = mock_instance

            result = await server.call_tool(
                "review_quality", {"diff": "test diff", "file_path": "test.py"}
            )
            assert "findings" in result
            assert result["agent"] == "quality"

    @pytest.mark.asyncio
    async def test_call_tool_review_testing(self):
        server = MCPServer()
        with patch("verdity.agents.testing.TestingAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            mock_instance.run = AsyncMock(return_value=mock_result)
            mock_agent.return_value = mock_instance

            result = await server.call_tool(
                "review_testing", {"diff": "test diff", "file_path": "test.py"}
            )
            assert "findings" in result
            assert result["agent"] == "testing"

    @pytest.mark.asyncio
    async def test_call_tool_review_documentation(self):
        server = MCPServer()
        with patch("verdity.agents.documentation.DocumentationAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            mock_instance.run = AsyncMock(return_value=mock_result)
            mock_agent.return_value = mock_instance

            result = await server.call_tool(
                "review_documentation", {"diff": "test diff", "file_path": "test.py"}
            )
            assert "findings" in result
            assert result["agent"] == "documentation"

    @pytest.mark.asyncio
    async def test_call_tool_review_full(self):
        server = MCPServer()
        with patch.object(server, "_orchestrator") as mock_orchestrator:
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            _mock_orchestrator_review(mock_orchestrator, mock_result)

            result = await server.call_tool(
                "review_full", {"diff": "test diff", "file_path": "test.py"}
            )
            assert "findings" in result
            assert result["agent"] == "full"

    @pytest.mark.asyncio
    async def test_call_tool_generate_fix(self):
        server = MCPServer()
        with patch("verdity.coding_agent.CodingAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_fix = MagicMock()
            mock_fix.suggested_lines = ["# fix"]
            mock_fix.explanation = "Test fix"
            mock_fix.patch = "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+# fix"
            mock_fix.confidence = 0.8
            mock_instance.generate_fix = AsyncMock(return_value=mock_fix)
            mock_agent.return_value = mock_instance

            result = await server.call_tool(
                "generate_fix",
                {
                    "finding": {"rule_id": "test", "message": "test", "file_path": "test.py"},
                    "diff": "test diff",
                },
            )
            assert "fix" in result
            assert "finding" in result


def test_create_mcp_server():
    server = create_mcp_server()
    assert isinstance(server, MCPServer)
    assert server.PROTOCOL_VERSION == "2024-11-05"


class TestMCPServerErrorPaths:
    """Test error handling paths in specialist review tools."""

    @pytest.mark.asyncio
    async def test_call_tool_review_security_error(self):
        """Test _review_security handles agent exceptions."""
        server = MCPServer()
        with patch("verdity.agents.security.SecurityAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(side_effect=Exception("Agent failed"))
            mock_agent.return_value = mock_instance

            result = await server.call_tool(
                "review_security", {"diff": "test diff", "file_path": "test.py"}
            )
            assert "error" in result
            assert "Agent failed" in result["error"]
            assert result["agent"] == "security"

    @pytest.mark.asyncio
    async def test_call_tool_review_quality_error(self):
        """Test _review_quality handles agent exceptions."""
        server = MCPServer()
        with patch("verdity.agents.code_quality.CodeQualityAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(side_effect=Exception("Agent failed"))
            mock_agent.return_value = mock_instance

            result = await server.call_tool(
                "review_quality", {"diff": "test diff", "file_path": "test.py"}
            )
            assert "error" in result
            assert "Agent failed" in result["error"]
            assert result["agent"] == "quality"

    @pytest.mark.asyncio
    async def test_call_tool_review_testing_error(self):
        """Test _review_testing handles agent exceptions."""
        server = MCPServer()
        with patch("verdity.agents.testing.TestingAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(side_effect=Exception("Agent failed"))
            mock_agent.return_value = mock_instance

            result = await server.call_tool(
                "review_testing", {"diff": "test diff", "file_path": "test.py"}
            )
            assert "error" in result
            assert "Agent failed" in result["error"]
            assert result["agent"] == "testing"

    @pytest.mark.asyncio
    async def test_call_tool_review_documentation_error(self):
        """Test _review_documentation handles agent exceptions."""
        server = MCPServer()
        with patch("verdity.agents.documentation.DocumentationAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(side_effect=Exception("Agent failed"))
            mock_agent.return_value = mock_instance

            result = await server.call_tool(
                "review_documentation", {"diff": "test diff", "file_path": "test.py"}
            )
            assert "error" in result
            assert "Agent failed" in result["error"]
            assert result["agent"] == "documentation"

    @pytest.mark.asyncio
    async def test_call_tool_review_full_error(self):
        """Test _review_full handles orchestrator exceptions."""
        server = MCPServer()
        with patch.object(server, "_orchestrator") as mock_orchestrator:
            mock_orchestrator.process_event = AsyncMock(
                side_effect=Exception("Orchestrator failed")
            )

            result = await server.call_tool(
                "review_full", {"diff": "test diff", "file_path": "test.py"}
            )
            assert "error" in result
            assert "Orchestrator failed" in result["error"]
            assert result["agent"] == "full"

    @pytest.mark.asyncio
    async def test_call_tool_apply_fix_reports_unimplemented(self):
        """call_tool surfaces the not-implemented status for apply_fix."""
        server = MCPServer()
        result = await server.call_tool("apply_fix", {"fix_patch": "patch", "file_path": "test.py"})
        assert "error" in result
        assert result["implemented"] is False
        assert result["tool"] == "apply_fix"

    @pytest.mark.asyncio
    async def test_call_tool_get_review_rules_exception(self):
        """Test call_tool catches exceptions from _get_review_rules."""
        server = MCPServer()
        with patch("verdity.review_rules.ReviewRules", side_effect=Exception("Rules init failed")):
            result = await server.call_tool("get_review_rules", {"repo_path": "/test/repo"})
            assert "error" in result
            assert "Rules init failed" in result["error"]
            assert result["tool"] == "get_review_rules"


class TestDiffToFiles:
    """Tests for _diff_to_files helper function."""

    def test_diff_to_files_empty_diff(self):
        """Test _diff_to_files with empty diff (line 43)."""
        from verdity.mcp_server import _diff_to_files

        result = _diff_to_files("")
        assert result == []

    def test_diff_to_files_with_file_path(self):
        """Test _diff_to_files with file_path (line 44-45)."""
        from verdity.mcp_server import _diff_to_files

        result = _diff_to_files("test diff", "test.py")
        assert result == [
            {"path": "test.py", "content": "test diff", "additions": "test diff", "deletions": ""}
        ]

    def test_diff_to_files_without_file_path(self):
        """Test _diff_to_files without file_path (line 46)."""
        from verdity.mcp_server import _diff_to_files

        result = _diff_to_files("test diff")
        assert result == [
            {"path": "unknown", "content": "test diff", "additions": "test diff", "deletions": ""}
        ]


class TestCallToolFallback:
    """Test the tool not implemented fallback (line 466)."""

    @pytest.mark.asyncio
    async def test_call_tool_not_implemented_fallback(self):
        """Test call_tool fallback when tool in list but not handled."""
        server = MCPServer()
        # Add a fake tool to the tools list but don't implement handler
        original_tools = server._tools
        server._tools = original_tools + [
            {"name": "fake_tool", "description": "Fake", "inputSchema": {}}
        ]

        try:
            result = await server.call_tool("fake_tool", {})
            assert "error" in result
            assert "Tool not implemented: fake_tool" in result["error"]
        finally:
            server._tools = original_tools


class TestMCPServerInitialize:
    """Tests for MCPServer initialize and shutdown."""

    @pytest.mark.asyncio
    async def test_initialize_creates_orchestrator(self):
        """Test initialize creates orchestrator."""
        server = MCPServer()
        # Mock the Orchestrator to avoid the config bug
        with patch("verdity.mcp_server.Orchestrator") as mock_orchestrator_class:
            mock_orchestrator = MagicMock()
            mock_orchestrator_class.return_value = mock_orchestrator

            await server.initialize()

            mock_orchestrator_class.assert_called_once()
            # Orchestrator exposes no async initialize(); construction is the
            # initialization step, and collaborators are passed explicitly.
            _, kwargs = mock_orchestrator_class.call_args
            assert set(kwargs) >= {
                "queue",
                "semantic_index",
                "token_economics",
                "audit_store",
            }
            assert server._orchestrator is mock_orchestrator

    @pytest.mark.asyncio
    async def test_shutdown_closes_collaborators(self):
        """Test shutdown closes the collaborators it opened.

        Orchestrator exposes no shutdown() method, so shutdown() closes the
        queue/index/economics/audit collaborators instead.
        """
        server = MCPServer()
        await server.initialize()
        server._audit = AsyncMock()

        await server.shutdown()

        server._audit.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_handles_none_orchestrator(self):
        """Test shutdown handles None orchestrator gracefully."""
        server = MCPServer()
        server._orchestrator = None

        # Should not raise
        await server.shutdown()


class TestReviewFullInitialize:
    """Test _review_full initialize path."""

    @pytest.mark.asyncio
    async def test_review_full_initializes_orchestrator(self):
        """Test _review_full calls initialize when _orchestrator is None."""
        server = MCPServer()
        server._orchestrator = None

        with patch("verdity.mcp_server.Orchestrator") as mock_orchestrator_class:
            mock_orchestrator = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            _mock_orchestrator_review(mock_orchestrator, mock_result)
            mock_orchestrator_class.return_value = mock_orchestrator

            result = await server.call_tool(
                "review_full", {"diff": "test diff", "file_path": "test.py"}
            )

            mock_orchestrator_class.assert_called_once()
            # Orchestrator has no async initialize(); collaborators are passed
            # to the constructor, which is what initializes it.
            _, init_kwargs = mock_orchestrator_class.call_args
            assert "queue" in init_kwargs and "audit_store" in init_kwargs
            assert "findings" in result


class TestVerdityReviewInitialize:
    """Test _verdity_review initialize path."""

    @pytest.mark.asyncio
    async def test_verdity_review_initializes_orchestrator(self):
        """Test _verdity_review calls initialize when _orchestrator is None."""
        server = MCPServer()
        server._orchestrator = None

        with (
            patch("verdity.github_client.GitHubClient") as mock_github_client,
            patch("verdity.config.get_settings") as mock_get_settings,
            patch("verdity.mcp_server.Orchestrator") as mock_orchestrator_class,
        ):
            mock_settings = MagicMock()
            mock_settings.github_app_id = "123"
            mock_settings.github_private_key = "key"
            mock_settings.github_installation_id = "456"
            mock_get_settings.return_value = mock_settings

            mock_client = AsyncMock()
            mock_client.get_pr_diff.return_value = {
                "files": [{"filename": "test.py", "patch": "diff", "additions": 1, "deletions": 0}],
                "base_sha": "abc123",
                "head_sha": "def456",
            }
            mock_github_client.return_value = mock_client

            mock_orchestrator = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            _mock_orchestrator_review(mock_orchestrator, mock_result)
            mock_orchestrator_class.return_value = mock_orchestrator

            result = await server.call_tool(
                "verdity_review", {"owner": "testorg", "repo": "testrepo", "pr_number": 42}
            )

            mock_orchestrator_class.assert_called_once()
            # Orchestrator has no async initialize(); collaborators are passed
            # to the constructor, which is what initializes it.
            _, init_kwargs = mock_orchestrator_class.call_args
            assert "queue" in init_kwargs and "audit_store" in init_kwargs
            assert "review_run_id" in result


class TestVerdityReview:
    """Tests for verdity_review tool (lines 694-791)."""

    @pytest.mark.asyncio
    async def test_verdity_review_basic(self):
        """Test basic verdity_review with PR diff."""
        server = MCPServer()
        with (
            patch("verdity.github_client.GitHubClient") as mock_github_client,
            patch("verdity.config.get_settings") as mock_get_settings,
            patch.object(server, "_orchestrator") as mock_orchestrator,
        ):
            # Mock settings
            mock_settings = MagicMock()
            mock_settings.github_app_id = "123"
            mock_settings.github_private_key = "key"
            mock_settings.github_installation_id = "456"
            mock_get_settings.return_value = mock_settings

            # Mock GitHub client
            mock_client = AsyncMock()
            mock_client.get_pr_diff.return_value = {
                "files": [
                    {
                        "filename": "test.py",
                        "patch": "@@ -1 +1 @@\n-old\n+new",
                        "additions": 1,
                        "deletions": 1,
                    }
                ],
                "base_sha": "abc123",
                "head_sha": "def456",
            }
            mock_github_client.return_value = mock_client

            # Mock orchestrator
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            _mock_orchestrator_review(mock_orchestrator, mock_result)

            result = await server.call_tool(
                "verdity_review",
                {"owner": "testorg", "repo": "testrepo", "pr_number": 42, "tier": "balanced"},
            )

            assert "review_run_id" in result
            assert result["pr_number"] == 42
            assert result["tier"] == "balanced"
            assert result["total_findings"] == 0
            mock_client.get_pr_diff.assert_called_once_with("testorg", "testrepo", 42)

    @pytest.mark.asyncio
    async def test_verdity_review_pr_not_found(self):
        """Test verdity_review when PR diff fetch returns None (404)."""
        server = MCPServer()
        with (
            patch("verdity.github_client.GitHubClient") as mock_github_client,
            patch("verdity.config.get_settings") as mock_get_settings,
            patch.object(server, "_orchestrator") as mock_orchestrator,
        ):
            mock_settings = MagicMock()
            mock_settings.github_app_id = "123"
            mock_settings.github_private_key = "key"
            mock_settings.github_installation_id = "456"
            mock_get_settings.return_value = mock_settings

            mock_client = AsyncMock()
            mock_client.get_pr_diff.return_value = None  # PR not found
            mock_github_client.return_value = mock_client

            # Mock orchestrator to avoid initialization bug
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            _mock_orchestrator_review(mock_orchestrator, mock_result)

            result = await server.call_tool(
                "verdity_review", {"owner": "testorg", "repo": "testrepo", "pr_number": 999}
            )

            assert "error" in result
            assert "Failed to fetch PR diff" in result["error"]
            assert result["pr_number"] == 999

    @pytest.mark.asyncio
    async def test_verdity_review_empty_diff(self):
        """Test verdity_review with empty diff files."""
        server = MCPServer()
        with (
            patch("verdity.github_client.GitHubClient") as mock_github_client,
            patch("verdity.config.get_settings") as mock_get_settings,
            patch.object(server, "_orchestrator") as mock_orchestrator,
        ):
            mock_settings = MagicMock()
            mock_settings.github_app_id = "123"
            mock_settings.github_private_key = "key"
            mock_settings.github_installation_id = "456"
            mock_get_settings.return_value = mock_settings

            mock_client = AsyncMock()
            mock_client.get_pr_diff.return_value = {
                "files": [],
                "base_sha": "abc123",
                "head_sha": "def456",
            }
            mock_github_client.return_value = mock_client

            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            _mock_orchestrator_review(mock_orchestrator, mock_result)

            result = await server.call_tool(
                "verdity_review", {"owner": "testorg", "repo": "testrepo", "pr_number": 42}
            )

            assert result["total_findings"] == 0

    @pytest.mark.asyncio
    async def test_verdity_review_tier_selection(self):
        """Test verdity_review tier selection logic (lite, balanced, deep)."""
        server = MCPServer()
        with (
            patch("verdity.github_client.GitHubClient") as mock_github_client,
            patch("verdity.config.get_settings") as mock_get_settings,
            patch.object(server, "_orchestrator") as mock_orchestrator,
        ):
            mock_settings = MagicMock()
            mock_settings.github_app_id = "123"
            mock_settings.github_private_key = "key"
            mock_settings.github_installation_id = "456"
            mock_get_settings.return_value = mock_settings

            mock_client = AsyncMock()
            mock_client.get_pr_diff.return_value = {
                "files": [{"filename": "test.py", "patch": "diff", "additions": 1, "deletions": 0}],
                "base_sha": "abc123",
                "head_sha": "def456",
            }
            mock_github_client.return_value = mock_client

            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            _mock_orchestrator_review(mock_orchestrator, mock_result)

            # Test lite tier
            result_lite = await server.call_tool(
                "verdity_review",
                {"owner": "testorg", "repo": "testrepo", "pr_number": 42, "tier": "lite"},
            )
            assert result_lite["tier"] == "lite"

            # Test deep tier
            result_deep = await server.call_tool(
                "verdity_review",
                {"owner": "testorg", "repo": "testrepo", "pr_number": 42, "tier": "deep"},
            )
            assert result_deep["tier"] == "deep"

            # The orchestrator derives its ReviewPolicy from the event (PR diff
            # size via resolve_policy), not from a caller-supplied tier — there
            # is no SpecialistContext to pass a policy through process_event().
            # So assert the requested tier is echoed back and a real event was
            # dispatched, rather than asserting a policy we cannot set.
            call_args = mock_orchestrator.process_event.call_args
            envelope = call_args[0][0]
            assert envelope.event.pull_request.number == 42
            assert result_deep["tier"] == "deep"

    @pytest.mark.asyncio
    async def test_verdity_review_post_to_github(self):
        """Test verdity_review with post_to_github=True."""
        server = MCPServer()
        with (
            patch("verdity.github_client.GitHubClient") as mock_github_client,
            patch("verdity.config.get_settings") as mock_get_settings,
            patch("verdity.github_client.create_check_output") as mock_create_check,
            patch.object(server, "_orchestrator") as mock_orchestrator,
        ):
            mock_settings = MagicMock()
            mock_settings.github_app_id = "123"
            mock_settings.github_private_key = "key"
            mock_settings.github_installation_id = "456"
            mock_get_settings.return_value = mock_settings

            mock_client = AsyncMock()
            mock_client.get_pr_diff.return_value = {
                "files": [{"filename": "test.py", "patch": "diff", "additions": 1, "deletions": 0}],
                "base_sha": "abc123",
                "head_sha": "def456",
            }
            mock_client.create_check_run.return_value = {"id": 123, "status": "completed"}
            mock_github_client.return_value = mock_client

            mock_create_check.return_value = {"title": "Verdity", "summary": "Done"}

            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            _mock_orchestrator_review(mock_orchestrator, mock_result)

            result = await server.call_tool(
                "verdity_review",
                {"owner": "testorg", "repo": "testrepo", "pr_number": 42, "post_to_github": True},
            )

            assert "github_check" in result
            assert result["github_check"]["id"] == 123
            mock_client.create_check_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_verdity_review_post_to_github_with_real_findings(self):
        """Posting a check run must work when findings are actually present.

        The existing post_to_github test used an empty findings list, so the
        list comprehension that rebuilds Finding objects never ran. With real
        findings it raised a 7-field ValidationError, which meant
        `review run` failed on its default path (--post-comment defaults on).
        """
        from verdity.schemas import Severity
        from verdity.schemas._models import Finding

        server = MCPServer()
        with (
            patch("verdity.github_client.GitHubClient") as mock_github_client,
            patch("verdity.config.get_settings") as mock_get_settings,
            patch.object(server, "_orchestrator") as mock_orchestrator,
        ):
            mock_settings = MagicMock()
            mock_settings.github_app_id = "123"
            mock_settings.github_private_key = "key"
            mock_settings.github_installation_id = "456"
            mock_get_settings.return_value = mock_settings

            mock_client = AsyncMock()
            mock_client.get_pr_diff.return_value = {
                "files": [{"filename": "test.py", "patch": "diff", "additions": 1, "deletions": 0}],
                "base_sha": "abc123",
                "head_sha": "def456",
            }
            mock_client.create_check_run.return_value = {"id": 123, "status": "completed"}
            mock_github_client.return_value = mock_client

            mock_result = MagicMock()
            mock_result.summary = "1 issue"
            mock_result.findings = [
                Finding(
                    concern="security",
                    severity=Severity.HIGH,
                    file="test.py",
                    line_start=26,
                    line_end=26,
                    summary="Path Traversal in updater",
                    explanation="tar member escapes the extraction dir",
                    confidence=0.9,
                    agent_version="v1",
                    prompt_hash="abc",
                )
            ]
            _mock_orchestrator_review(mock_orchestrator, mock_result)

            result = await server.call_tool(
                "verdity_review",
                {
                    "owner": "testorg",
                    "repo": "testrepo",
                    "pr_number": 42,
                    "post_to_github": True,
                },
            )

            assert "error" not in result, result.get("error")
            assert result["total_findings"] == 1
            assert "github_check" in result
            # The check run must carry the finding, not an empty list.
            posted = mock_client.create_check_run.call_args.kwargs["output"]
            assert posted["annotations"], "check run lost the finding"

    @pytest.mark.asyncio
    async def test_verdity_review_skips_unconvertible_finding(self):
        """One malformed finding must not cost the whole check run."""
        server = MCPServer()
        with (
            patch("verdity.github_client.GitHubClient") as mock_github_client,
            patch("verdity.config.get_settings") as mock_get_settings,
            patch.object(server, "_orchestrator") as mock_orchestrator,
        ):
            mock_settings = MagicMock()
            mock_settings.github_app_id = "123"
            mock_settings.github_private_key = "key"
            mock_settings.github_installation_id = "456"
            mock_get_settings.return_value = mock_settings

            mock_client = AsyncMock()
            mock_client.get_pr_diff.return_value = {
                "files": [{"filename": "t.py", "patch": "d", "additions": 1, "deletions": 0}],
                "base_sha": "abc123",
                "head_sha": "def456",
            }
            mock_client.create_check_run.return_value = {"id": 1}
            mock_github_client.return_value = mock_client

            from verdity.schemas import Severity
            from verdity.schemas._models import Finding

            # Two specialists: one whose finding cannot be converted (blank
            # message) and one that is fine.
            bad_finding = MagicMock()
            bad_finding.summary = ""
            bad_finding.file = "t.py"
            bad_finding.line_start = 1
            bad_finding.severity = Severity.LOW
            bad_finding.confidence = 0.1
            bad_specialist = MagicMock()
            bad_specialist.findings = [bad_finding]

            good_finding = Finding(
                concern="security",
                severity=Severity.HIGH,
                file="t.py",
                line_start=5,
                line_end=5,
                summary="Path traversal",
                explanation="x",
                confidence=0.8,
                agent_version="v1",
                prompt_hash="h",
            )
            good_specialist = MagicMock()
            good_specialist.findings = [good_finding]

            run = MagicMock()
            run.specialist_results = {
                "unknownspecialist": bad_specialist,
                "security": good_specialist,
            }
            mock_orchestrator.process_event = AsyncMock(return_value=uuid.uuid4())
            mock_orchestrator.get_run = MagicMock(return_value=run)

            result = await server.call_tool(
                "verdity_review",
                {
                    "owner": "testorg",
                    "repo": "testrepo",
                    "pr_number": 42,
                    "post_to_github": True,
                },
            )

            assert "error" not in result, result.get("error")
            posted = mock_client.create_check_run.call_args.kwargs["output"]
            # Only the good finding survives, and the check run still posts.
            assert len(posted["annotations"]) == 1

    @pytest.mark.asyncio
    async def test_verdity_review_exception_handling(self):
        """Test verdity_review handles exceptions gracefully."""
        server = MCPServer()
        with (
            patch("verdity.github_client.GitHubClient") as mock_github_client,
            patch("verdity.config.get_settings") as mock_get_settings,
            patch.object(server, "_orchestrator") as mock_orchestrator,
        ):
            mock_settings = MagicMock()
            mock_settings.github_app_id = "123"
            mock_settings.github_private_key = "key"
            mock_settings.github_installation_id = "456"
            mock_get_settings.return_value = mock_settings

            mock_client = AsyncMock()
            mock_client.get_pr_diff.side_effect = Exception("GitHub API error")
            mock_github_client.return_value = mock_client

            # Mock orchestrator to avoid initialization bug
            mock_result = MagicMock()
            mock_result.findings = []
            mock_result.summary = "No findings"
            _mock_orchestrator_review(mock_orchestrator, mock_result)

            result = await server.call_tool(
                "verdity_review", {"owner": "testorg", "repo": "testrepo", "pr_number": 42}
            )

            assert "error" in result
            assert "GitHub API error" in result["error"]


class TestVerdityEnforce:
    """Tests for verdity_enforce tool (lines 795-844)."""

    @pytest.mark.asyncio
    async def test_verdity_enforce_custom_rules_file(self):
        """Test verdity_enforce loading rules from custom file."""
        server = MCPServer()
        rules_yaml = """
rules:
  - id: "test-rule"
    when: "finding.severity == 'critical'"
    then: "BLOCK"
    message: "Critical finding blocked"
    priority: 10
    enabled: true
"""
        with patch("builtins.open", mock_open(read_data=rules_yaml)):
            result = await server.call_tool(
                "verdity_enforce",
                {
                    "finding": {
                        "file_path": "test.py",
                        "line_start": 10,
                        "severity": "critical",
                        "confidence": 0.9,
                        "concern": "security",
                        "summary": "Test finding",
                        "explanation": "Details",
                    },
                    "rules_file": "/custom/rules.yml",
                },
            )

            assert "action" in result
            assert result["action"] == "BLOCK"

    @pytest.mark.asyncio
    async def test_verdity_enforce_fallback_to_default_rules(self):
        """Test verdity_enforce falls back to .verdity/rules.yml."""
        server = MCPServer()
        with patch("verdity.enforcement.load_rules_from_yaml") as mock_load_rules:
            mock_rule = MagicMock()
            mock_rule.id = "default-rule"
            mock_rule.when = "finding.severity == 'high'"
            mock_rule.then = MagicMock()
            mock_rule.then.value = "REQUIRE_APPROVAL"
            mock_load_rules.return_value = [mock_rule]

            with patch("verdity.enforcement.EnforcementEngine") as mock_engine_class:
                from verdity.enforcement import EnforcementDecision

                mock_engine = AsyncMock()
                mock_decision = EnforcementDecision(
                    action="require_approval", rule_id="default-rule", message="Requires approval"
                )
                mock_engine.evaluate_with_context.return_value = mock_decision
                mock_engine_class.return_value = mock_engine

                result = await server.call_tool(
                    "verdity_enforce",
                    {
                        "finding": {
                            "file_path": "test.py",
                            "line_start": 10,
                            "severity": "high",
                            "confidence": 0.8,
                            "concern": "security",
                            "summary": "Test finding",
                        }
                    },
                )

                assert result["action"] == "REQUIRE_APPROVAL"
                mock_load_rules.assert_called_once_with(".verdity/rules.yml")

    @pytest.mark.asyncio
    async def test_verdity_enforce_no_rules_file(self):
        """Test verdity_enforce when no rules file exists."""
        server = MCPServer()
        with patch("verdity.enforcement.load_rules_from_yaml", side_effect=FileNotFoundError()):
            with patch("verdity.enforcement.EnforcementEngine") as mock_engine_class:
                from verdity.enforcement import EnforcementDecision

                mock_engine = AsyncMock()
                mock_decision = EnforcementDecision(action="allow")
                mock_engine.evaluate_with_context.return_value = mock_decision
                mock_engine_class.return_value = mock_engine

                result = await server.call_tool(
                    "verdity_enforce",
                    {
                        "finding": {
                            "file_path": "test.py",
                            "line_start": 10,
                            "severity": "low",
                            "confidence": 0.5,
                            "concern": "code_quality",
                            "summary": "Test finding",
                        }
                    },
                )

                assert result["action"] == "ALLOW"

    @pytest.mark.asyncio
    async def test_verdity_enforce_various_finding_types(self):
        """Test verdity_enforce with different finding types."""
        server = MCPServer()
        with patch("verdity.enforcement.load_rules_from_yaml", side_effect=FileNotFoundError()):
            with patch("verdity.enforcement.EnforcementEngine") as mock_engine_class:
                from verdity.enforcement import EnforcementDecision

                mock_engine = AsyncMock()
                mock_decision = EnforcementDecision(action="allow")
                mock_engine.evaluate_with_context.return_value = mock_decision
                mock_engine_class.return_value = mock_engine

                # Test security finding
                result = await server.call_tool(
                    "verdity_enforce",
                    {
                        "finding": {
                            "file": "test.py",
                            "line": 10,
                            "line_start": 10,
                            "line_end": 15,
                            "severity": "critical",
                            "confidence": 0.95,
                            "concern": "security",
                            "summary": "SQL injection",
                            "explanation": "User input not sanitized",
                        }
                    },
                )
                assert result["finding"]["concern"] == "security"

                # Test testing finding
                result = await server.call_tool(
                    "verdity_enforce",
                    {
                        "finding": {
                            "file_path": "test.py",
                            "line_start": 10,
                            "severity": "medium",
                            "confidence": 0.7,
                            "concern": "testing",
                            "summary": "Missing test",
                        }
                    },
                )
                assert result["finding"]["concern"] == "testing"

    @pytest.mark.asyncio
    async def test_verdity_enforce_exception_handling(self):
        """Test verdity_enforce handles exceptions."""
        server = MCPServer()
        with patch("verdity.enforcement.load_rules_from_yaml", side_effect=FileNotFoundError()):
            with patch("verdity.enforcement.EnforcementEngine") as mock_engine_class:
                mock_engine = AsyncMock()
                mock_engine.evaluate_with_context.side_effect = Exception("Evaluation error")
                mock_engine_class.return_value = mock_engine

                result = await server.call_tool(
                    "verdity_enforce",
                    {
                        "finding": {
                            "file_path": "test.py",
                            "line_start": 10,
                            "severity": "medium",
                            "confidence": 0.5,
                            "concern": "code_quality",
                            "summary": "Test",
                        }
                    },
                )

                assert "error" in result
                assert "Evaluation error" in result["error"]


class TestVerdityRulesList:
    """Tests for verdity_rules_list tool (lines 848-867)."""

    @pytest.mark.asyncio
    async def test_verdity_rules_list_success(self):
        """Test verdity_rules_list returns rules from file."""
        server = MCPServer()
        rules_data = {
            "rules": [
                {"id": "rule1", "when": "severity == 'critical'", "then": "BLOCK"},
                {"id": "rule2", "when": "severity == 'high'", "then": "REQUIRE_APPROVAL"},
            ]
        }
        with patch("builtins.open", mock_open(read_data=yaml.dump(rules_data))):
            result = await server.call_tool("verdity_rules_list", {"repo_path": "/test/repo"})

            assert result["repo_path"] == "/test/repo"
            assert len(result["rules"]) == 2
            assert result["rules"][0]["id"] == "rule1"

    @pytest.mark.asyncio
    async def test_verdity_rules_list_file_not_found(self):
        """Test verdity_rules_list when rules file not found."""
        server = MCPServer()
        with patch("builtins.open", side_effect=FileNotFoundError()):
            result = await server.call_tool(
                "verdity_rules_list", {"repo_path": "/nonexistent/repo"}
            )

            assert "error" in result
            assert "Rules file not found" in result["error"]
            assert result["repo_path"] == "/nonexistent/repo"

    @pytest.mark.asyncio
    async def test_verdity_rules_list_yaml_parse_error(self):
        """Test verdity_rules_list handles YAML parse errors."""
        server = MCPServer()
        with patch("builtins.open", mock_open(read_data="invalid: yaml: : :")):
            result = await server.call_tool("verdity_rules_list", {"repo_path": "/test/repo"})

            assert "error" in result

    @pytest.mark.asyncio
    async def test_verdity_rules_list_missing_rules_key(self):
        """Test verdity_rules_list when rules key is missing."""
        server = MCPServer()
        rules_data = {"other_key": "value"}  # No "rules" key
        with patch("builtins.open", mock_open(read_data=yaml.dump(rules_data))):
            result = await server.call_tool("verdity_rules_list", {"repo_path": "/test/repo"})

            assert result["rules"] == []


class TestVerdityReviewStatus:
    """Tests for verdity_review_status tool (lines 873-875)."""

    @pytest.mark.asyncio
    async def test_verdity_review_status_placeholder(self):
        """Test verdity_review_status returns placeholder (not implemented)."""
        server = MCPServer()
        result = await server.call_tool(
            "verdity_review_status", {"review_run_id": "test-uuid-1234"}
        )

        assert result["review_run_id"] == "test-uuid-1234"
        assert result["status"] == "unknown"
        assert "not yet implemented" in result["message"]


class TestApplyFix:
    """Tests for apply_fix tool (lines 899-914)."""

    @pytest.mark.asyncio
    async def test_apply_fix_reports_not_implemented(self):
        """apply_fix must say it is unimplemented instead of raising AttributeError.

        GitHubClient has no apply_fix(), so this advertised MCP tool could only
        ever fail. These tests previously mocked GitHubClient with an AsyncMock,
        which invents any attribute, so they passed against a method that does
        not exist.
        """
        server = MCPServer()

        result = await server.call_tool(
            "apply_fix",
            {
                "fix_patch": "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new",
                "file_path": "test.py",
                "branch": "feature/test",
                "commit_message": "fix: apply fix",
            },
        )

        assert result["implemented"] is False
        assert "not implemented" in result["error"]
        assert result["file_path"] == "test.py"

    @pytest.mark.asyncio
    async def test_apply_fix_error_names_the_missing_capability(self):
        """The error must point at the actual missing client method."""
        from verdity.github_client import GitHubClient

        assert not hasattr(GitHubClient, "apply_fix")

        server = MCPServer()
        result = await server.call_tool("apply_fix", {"fix_patch": "patch", "file_path": "test.py"})
        assert "apply_fix()" in result["error"]


class TestGetReviewRules:
    """Tests for get_review_rules tool (lines 918-924)."""

    @pytest.mark.asyncio
    async def test_get_review_rules_basic(self):
        """Test get_review_rules returns rules from ReviewRules."""
        server = MCPServer()
        with patch("verdity.review_rules.ReviewRules") as mock_review_rules:
            mock_rules_instance = MagicMock()
            mock_rules_instance.get_rules.return_value = {"rules": ["rule1", "rule2"]}
            mock_review_rules.return_value = mock_rules_instance

            result = await server.call_tool(
                "get_review_rules", {"repo_path": "/test/repo", "file_path": "src/test.py"}
            )

            assert result["rules"] == ["rule1", "rule2"]
            mock_review_rules.assert_called_once_with("/test/repo")
            mock_rules_instance.get_rules.assert_called_once_with("src/test.py")

    @pytest.mark.asyncio
    async def test_get_review_rules_no_file_path(self):
        """Test get_review_rules without file_path."""
        server = MCPServer()
        with patch("verdity.review_rules.ReviewRules") as mock_review_rules:
            mock_rules_instance = MagicMock()
            mock_rules_instance.get_rules.return_value = {"rules": ["rule1"]}
            mock_review_rules.return_value = mock_rules_instance

            result = await server.call_tool("get_review_rules", {"repo_path": "/test/repo"})

            assert result["rules"] == ["rule1"]
            mock_rules_instance.get_rules.assert_called_once_with("")


class TestMcpUsesRealGitHubClientApi:
    """Guard: the MCP review path must only call methods GitHubClient has.

    An AsyncMock happily invents any attribute, so a call to a non-existent
    method (e.g. post_check_run) passed every mocked test and only blew up
    against the real client with AttributeError. These assertions check the
    names against the real class.
    """

    def test_check_run_method_names_exist_on_real_client(self):
        from verdity.github_client import GitHubClient

        assert hasattr(GitHubClient, "create_check_run")
        assert hasattr(GitHubClient, "update_check_run")
        assert not hasattr(GitHubClient, "post_check_run")

    def test_mcp_source_only_references_real_client_methods(self):
        import inspect
        import re

        from verdity import mcp_server
        from verdity.github_client import GitHubClient

        source = inspect.getsource(mcp_server)
        called = set(re.findall(r"client\.([a-zA-Z_][a-zA-Z0-9_]*)\(", source))
        # Only methods, not attributes assigned/passed around.
        real = {n for n in dir(GitHubClient) if not n.startswith("_")}
        missing = {c for c in called if c not in real and c not in {"close"}}
        assert not missing, f"mcp_server calls methods GitHubClient lacks: {missing}"

    @pytest.mark.asyncio
    async def test_apply_fix_warns_if_client_gains_apply_fix(self):
        """If GitHubClient ever gains apply_fix, _apply_fix must still be honest.

        Guards against the tool silently continuing to claim "not implemented"
        after the underlying capability appears.
        """
        server = MCPServer()
        real = verdity.github_client.GitHubClient

        class FutureClient(real):  # type: ignore[misc, valid-type]
            async def apply_fix(self, **kwargs):
                return {"ok": True}

        with patch("verdity.github_client.GitHubClient", FutureClient):
            with patch.object(verdity.mcp_server.logger, "warning") as mock_warning:
                result = await server.call_tool(
                    "apply_fix", {"fix_patch": "p", "file_path": "f.py"}
                )

        assert result["implemented"] is False
        assert any(
            "needs a real implementation" in str(c.args[0]) for c in mock_warning.call_args_list
        )


class TestFindingFromFlattened:
    """_finding_from_flattened() must never lose a finding to a bad value."""

    def test_maps_transport_keys_onto_full_finding(self):
        from verdity.mcp_server import _finding_from_flattened

        f = _finding_from_flattened(
            {
                "rule_id": "security-0",
                "message": "Path traversal",
                "file_path": "gui/updater.py",
                "line": 26,
                "severity": "high",
                "confidence": 0.9,
            },
            owner="o",
            repo="r",
        )

        assert f.summary == "Path traversal"
        assert f.file == "gui/updater.py"
        assert f.line_start == 26
        assert f.line_end == 26
        assert f.concern.value == "security"
        assert f.severity.value == "high"
        assert f.confidence == 0.9
        assert f.evidence[0].tool == "verdity/o/r"

    def test_unknown_severity_falls_back_to_info(self):
        from verdity.mcp_server import _finding_from_flattened

        f = _finding_from_flattened(
            {"rule_id": "quality-1", "message": "m", "severity": "catastrophic"},
            owner="o",
            repo="r",
        )
        assert f.severity.value == "info"

    def test_unknown_concern_falls_back_to_code_quality(self):
        from verdity.mcp_server import _finding_from_flattened

        f = _finding_from_flattened(
            {"rule_id": "no-known-specialist-2", "message": "m"},
            owner="o",
            repo="r",
        )
        assert f.concern.value == "code_quality"

    def test_missing_optional_fields_get_safe_defaults(self):
        from verdity.mcp_server import _finding_from_flattened

        f = _finding_from_flattened(
            {"rule_id": "x-0", "message": "m", "file_path": "", "line": 0},
            owner="o",
            repo="r",
        )
        # line_start has ge=1, so a 0 must be coerced, not passed through.
        assert f.line_start >= 1
        assert f.file == "unknown"

    def test_blank_message_is_rejected(self):
        """A finding with no message is unusable; the caller skips it."""
        from verdity.mcp_server import _finding_from_flattened

        with pytest.raises(ValueError, match="no message"):
            _finding_from_flattened(
                {"rule_id": "security-0", "message": "   "},
                owner="o",
                repo="r",
            )
