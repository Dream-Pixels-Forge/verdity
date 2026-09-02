"""Tests for MCP Server module."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdity.mcp_server import MCPServer, create_mcp_server


class TestMCPServer:
    def test_init_defaults(self):
        server = MCPServer()
        assert server.PROTOCOL_VERSION == "2024-11-05"
        assert server.SERVER_INFO["name"] == "verdity"
        assert server.SERVER_INFO["version"] == "0.3.0"
        assert len(server._tools) == 8

    def test_get_tools(self):
        server = MCPServer()
        tools = server.get_tools()
        assert len(tools) == 8
        tool_names = [t["name"] for t in tools]
        assert "review_security" in tool_names
        assert "review_quality" in tool_names
        assert "review_testing" in tool_names
        assert "review_documentation" in tool_names
        assert "review_full" in tool_names
        assert "generate_fix" in tool_names
        assert "apply_fix" in tool_names
        assert "get_review_rules" in tool_names

    def test_get_server_info(self):
        server = MCPServer()
        info = server.get_server_info()
        assert info["name"] == "verdity"
        assert info["version"] == "0.3.0"
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
                "review_security",
                {"diff": "test diff", "file_path": "test.py"}
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
                "review_quality",
                {"diff": "test diff", "file_path": "test.py"}
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
                "review_testing",
                {"diff": "test diff", "file_path": "test.py"}
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
                "review_documentation",
                {"diff": "test diff", "file_path": "test.py"}
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
            mock_orchestrator.review = AsyncMock(return_value=mock_result)

            result = await server.call_tool(
                "review_full",
                {"diff": "test diff", "file_path": "test.py"}
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
                    "diff": "test diff"
                }
            )
            assert "fix" in result
            assert "finding" in result


def test_create_mcp_server():
    server = create_mcp_server()
    assert isinstance(server, MCPServer)
    assert server.PROTOCOL_VERSION == "2024-11-05"
