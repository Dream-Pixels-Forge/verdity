"""Additional MCP Server tests covering missing branches in mcp_server.py."""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdity.mcp_server import MCPServer, _diff_to_files


def _mock_orchestrator_review(mock_orchestrator, mock_result):
    """Mock the real entry point: process_event() + get_run() (no review())."""
    import uuid as _uuid

    run = MagicMock()
    run.specialist_results = {"security": mock_result}
    mock_orchestrator.process_event = AsyncMock(return_value=_uuid.uuid4())
    mock_orchestrator.get_run = MagicMock(return_value=run)
    return mock_orchestrator


class TestDiffFilesReachSpecialists:
    """diff_files must actually reach the specialists.

    _run_specialist() builds SpecialistContext with `diff_files=[]` and the
    comment "populated by caller or extracted from event" — but nothing did
    either. Every specialist therefore scanned an empty diff and every review
    returned zero findings regardless of the input, i.e. a false clean bill of
    health. The caller must be able to supply diff files.
    """

    def test_queue_envelope_accepts_diff_files(self):
        """QueueEnvelope is the carrier; it needs the field."""
        import uuid as _uuid

        from verdity.schemas._models import QueueEnvelope, RepoRef, TriggerType, VerdityEvent

        envelope = QueueEnvelope(
            event=VerdityEvent(
                delivery_id=str(_uuid.uuid4()),
                trigger_type=TriggerType.PR_SYNCHRONIZE,
                repo=RepoRef(owner="o", name="r", id=1),
            ),
            diff_files=[{"path": "main.c"}],
        )
        assert envelope.diff_files == [{"path": "main.c"}]

    @pytest.mark.asyncio
    async def test_run_orchestrator_passes_diff_files_through(self):
        """The diff given to _run_orchestrator must reach process_event."""
        from verdity.mcp_server import MCPServer

        server = MCPServer()
        with patch.object(server, "_orchestrator") as mock_orch:
            import uuid as _uuid

            mock_orch.process_event = AsyncMock(return_value=_uuid.uuid4())
            mock_orch.get_run = MagicMock(
                return_value=MagicMock(specialist_results={}, status="completed")
            )

            await server._run_orchestrator(
                owner="o",
                repo="r",
                pr_number=1,
                diff="+ char buf[10]; strcpy(buf, argv[1]);",
                file_path="main.c",
            )

            envelope = mock_orch.process_event.call_args[0][0]
            assert envelope.diff_files, "diff_files never reached the orchestrator"
            assert any("main.c" in str(d) for d in envelope.diff_files)

    @pytest.mark.asyncio
    async def test_run_orchestrator_accepts_prebuilt_diff_files(self):
        """_verdity_review already builds diff_files from the GitHub API.

        Those entries must be usable directly; rebuilding them from a raw diff
        string is lossy for per-file patches, so the prebuilt list is passed
        through instead of being dropped on the floor.
        """
        from verdity.mcp_server import MCPServer

        server = MCPServer()
        with patch.object(server, "_orchestrator") as mock_orch:
            import uuid as _uuid

            mock_orch.process_event = AsyncMock(return_value=_uuid.uuid4())
            mock_orch.get_run = MagicMock(
                return_value=MagicMock(specialist_results={}, status="completed")
            )

            await server._run_orchestrator(
                owner="o",
                repo="r",
                pr_number=1,
                diff_files=[
                    {
                        "path": "gui/updater.py",
                        "content": "+ os.system(x)",
                        "additions": 1,
                        "deletions": 0,
                    }
                ],
            )

            envelope = mock_orch.process_event.call_args[0][0]
            assert envelope.diff_files, "prebuilt diff_files were dropped"
            assert envelope.diff_files[0]["path"] == "gui/updater.py"


class TestOrchestratorWiring:
    """initialize() must build a real, usable Orchestrator.

    These tests deliberately do NOT mock Orchestrator. The pre-existing
    lifecycle tests above mock it, which is exactly why the wrong constructor
    signature (`config=`, plus non-existent `initialize()`/`shutdown()` methods)
    survived: a mock accepts any call. Here the real class is constructed, so a
    signature mismatch fails loudly.
    """

    @pytest.mark.asyncio
    async def test_initialize_uses_real_orchestrator_constructor(self):
        from verdity.orchestrator import Orchestrator

        server = MCPServer()
        await server.initialize()

        # Real instance, built without TypeError.
        assert isinstance(server._orchestrator, Orchestrator)

    @pytest.mark.asyncio
    async def test_shutdown_does_not_raise_on_real_orchestrator(self):
        server = MCPServer()
        await server.initialize()
        # Orchestrator exposes no shutdown(); this must not raise.
        await server.shutdown()

    @pytest.mark.asyncio
    async def test_specialists_are_registered(self):
        """A review is meaningless without registered specialists.

        worker.py registers security/code_quality/testing/documentation on the
        orchestrator it builds. The MCP path never did, so process_event() had
        nothing to dispatch to.
        """
        server = MCPServer()
        await server.initialize()
        registered = set(server._orchestrator._specialists)
        assert registered, "no specialists registered — review would find nothing"
        assert "security" in registered


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
    """initialize() and shutdown() — both branches exercised.

    These previously asserted `orchestrator.initialize()` / `.shutdown()` were
    awaited. Orchestrator has neither method, so those assertions only ever
    passed because the orchestrator was mocked. They now assert the real
    contract: construction happens, and shutdown() closes collaborators.
    """

    @pytest.mark.asyncio
    async def test_initialize_creates_orchestrator(self):
        server = MCPServer()
        with patch("verdity.mcp_server.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch_cls.return_value = mock_orch
            await server.initialize()
            assert server._orchestrator is mock_orch
            # Constructed with collaborators, not a config object.
            _, kwargs = mock_orch_cls.call_args
            assert set(kwargs) >= {
                "queue",
                "semantic_index",
                "token_economics",
                "audit_store",
            }

    @pytest.mark.asyncio
    async def test_shutdown_closes_collaborators(self):
        server = MCPServer()
        await server.initialize()
        server._audit = MagicMock()
        server._audit.close = AsyncMock()
        await server.shutdown()
        server._audit.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_without_orchestrator(self):
        server = MCPServer()
        server._orchestrator = None
        # Must not raise
        await server.shutdown()


class TestCallToolExceptions:
    """Cover the except branches and the _review_full orchestrator-not-yet-initialized path."""

    @staticmethod
    def _mock_finding(summary: str = "issue", severity_value: str = "high"):
        f = MagicMock()
        f.summary = summary
        f.file = "x.py"
        f.line_start = 1
        f.severity = MagicMock()
        f.severity.value = severity_value
        f.confidence = 0.5
        return f

    @pytest.mark.asyncio
    async def test_call_tool_review_security_returns_findings(self):
        """Cover the success path with non-empty findings."""
        server = MCPServer()
        with patch("verdity.agents.security.SecurityAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = [self._mock_finding()]
            mock_result.summary = "summary"
            mock_instance.run = AsyncMock(return_value=mock_result)
            mock_agent.return_value = mock_instance
            result = await server.call_tool(
                "review_security",
                {"diff": "test", "file_path": "x.py"},
            )
            assert result["findings"][0]["rule_id"] == "security-0"
            assert result["agent"] == "security"

    @pytest.mark.asyncio
    async def test_call_tool_review_quality_returns_findings(self):
        server = MCPServer()
        with patch("verdity.agents.code_quality.CodeQualityAgent") as mock_agent:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.findings = [self._mock_finding()]
            mock_result.summary = "summary"
            mock_instance.run = AsyncMock(return_value=mock_result)
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
            mock_result = MagicMock()
            mock_result.findings = [self._mock_finding()]
            mock_result.summary = "summary"
            mock_instance.run = AsyncMock(return_value=mock_result)
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
            mock_result = MagicMock()
            mock_result.findings = [self._mock_finding()]
            mock_result.summary = "summary"
            mock_instance.run = AsyncMock(return_value=mock_result)
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
            assert result["agent"] == "documentation"

    @pytest.mark.asyncio
    async def test_call_tool_review_full_raises(self):
        server = MCPServer()
        with patch("verdity.mcp_server.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.initialize = AsyncMock()
            mock_orch.process_event = AsyncMock(side_effect=RuntimeError("boom"))
            mock_orch_cls.return_value = mock_orch
            result = await server.call_tool(
                "review_full",
                {"diff": "test", "file_path": "x.py"},
            )
            assert "error" in result
            assert result["agent"] == "full"

    @pytest.mark.asyncio
    async def test_call_tool_apply_fix(self):
        """apply_fix reports that it is not implemented.

        GitHubClient has no apply_fix(), so mocking the class let this test
        pass against a method that does not exist.
        """
        server = MCPServer()
        result = await server.call_tool(
            "apply_fix",
            {"fix_patch": "+ x", "file_path": "x.py"},
        )
        assert result["implemented"] is False
        assert "not implemented" in result["error"]

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

            # Review returns a result with findings
            finding_mock = MagicMock()
            finding_mock.summary = "issue"
            finding_mock.file = "x.py"
            finding_mock.line_start = 1
            finding_mock.severity = MagicMock()
            finding_mock.severity.value = "high"
            finding_mock.confidence = 0.5
            mock_result = MagicMock()
            mock_result.findings = [finding_mock]
            mock_result.summary = "summary"
            _mock_orchestrator_review(mock_orch, mock_result)

            mock_orch_cls.return_value = mock_orch
            result = await server.call_tool("review_full", {"diff": "x", "file_path": "x.py"})
            # Orchestrator has no async initialize(); construction is what
            # initializes it, so assert the collaborators were supplied.
            _, kwargs = mock_orch_cls.call_args
            assert "queue" in kwargs and "audit_store" in kwargs
            assert result["agent"] == "full"
            # rule_id is prefixed by the specialist that produced the finding,
            # so it identifies the source agent rather than a flat "full-N".
            assert result["findings"][0]["rule_id"] == "security-0"


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
