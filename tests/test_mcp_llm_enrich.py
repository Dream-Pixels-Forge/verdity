"""Tests for _llm_enrich_findings MCP tool.

TDD tests for SG-02: Fix MCP llm_enrich_findings crash.
These tests verify the actual implementation fixes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helper: Create test finding data ───────────────────────────────────────


def create_finding_data(
    finding_id: str = "123e4567-e89b-12d3-a456-426614174000",
    concern: str = "security",
    severity: str = "high",
    file: str = "test.py",
    line_start: int = 10,
    line_end: int = 10,
    summary: str = "Test finding",
    explanation: str = "Test explanation",
    confidence: float = 0.8,
) -> dict:
    """Create a valid finding data dict for the MCP tool."""
    return {
        "finding_id": finding_id,
        "concern": concern,
        "severity": severity,
        "file": file,
        "line_start": line_start,
        "line_end": line_end,
        "summary": summary,
        "explanation": explanation,
        "confidence": confidence,
    }


# ── Test: Bad input returns structured error, not traceback ─────────────


@pytest.mark.asyncio
async def test_llm_enrich_missing_required_field():
    """Test that missing required field returns structured error."""
    from verdity.mcp_server import MCPServer

    server = MCPServer()
    result = await server._llm_enrich_findings(
        {
            "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
            "findings": [],
            "diff": "test diff",
            "mode": "explain",
        }
    )
    assert "error" in result
    assert "required" in result["error"].lower()


@pytest.mark.asyncio
async def test_llm_enrich_invalid_finding_field():
    """Test that invalid concern type returns structured error."""
    from verdity.mcp_server import MCPServer

    server = MCPServer()
    result = await server._llm_enrich_findings(
        {
            "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
            "findings": [
                {
                    "finding_id": "abc-123",
                    "concern": "invalid_concern_type",  # Will fail ConcernType validation
                    "severity": "high",
                    "file": "test.py",
                    "line_start": 10,
                    "line_end": 10,
                    "summary": "Test finding",
                    "explanation": "Test explanation",
                    "confidence": 0.8,
                }
            ],
            "diff": "test diff",
            "mode": "explain",
        }
    )
    # Should return structured error, not raise exception
    assert "error" in result
    assert "Invalid concern type" in result["error"]


@pytest.mark.asyncio
async def test_llm_enrich_missing_field():
    """Test that missing required field in finding returns structured error."""
    from verdity.mcp_server import MCPServer

    server = MCPServer()
    result = await server._llm_enrich_findings(
        {
            "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
            "findings": [
                {
                    "finding_id": "abc-123",
                    "severity": "high",
                    "file": "test.py",
                    "summary": "Test finding",
                }
            ],
            "diff": "test diff",
            "mode": "explain",
        }
    )
    # Should return structured error, not raise exception
    assert "error" in result
    assert "Missing required fields" in result["error"]


# ── Test: Non-UUID finding_id handling ────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_enrich_non_uuid_finding_id_kept():
    """Test that non-UUID finding_id is kept and returned unchanged."""
    from verdity.mcp_server import MCPServer

    server = MCPServer()

    # Create a mock result with the original finding_id
    mock_result = MagicMock()
    mock_result.finding_id = "abc-123-def-456"  # Original string ID kept
    mock_result.mode.value = "explain"
    mock_result.model = "qwen2.5-7b-instruct"
    mock_result.backend = "ollama"
    mock_result.explanation = "Test explanation"
    mock_result.confidence_adjustment_rationale = "Test rationale"
    mock_result.fix_diff = None
    mock_result.fix_explanation = None
    mock_result.risk_score = None
    mock_result.cwe_ids = None
    mock_result.exploitability = None
    mock_result.blast_radius = None

    with patch("verdity.agents.llm_enricher.LLMEnricherAgent") as mock_enricher_class:
        mock_enricher = AsyncMock()
        mock_enricher.enrich_findings = AsyncMock(return_value=[mock_result])
        mock_enricher_class.return_value = mock_enricher

        with patch("verdity.audit_store.AuditStore") as mock_audit_class:
            mock_audit = AsyncMock()
            mock_audit.connect = AsyncMock()
            mock_audit.close = AsyncMock()
            mock_audit_class.return_value = mock_audit

            with patch("verdity.token_economics.TokenEconomicsService"):
                # Patch LocalLLMGateway with proper async close
                mock_gateway_instance = MagicMock()
                mock_gateway_instance.close = AsyncMock()
                with patch(
                    "verdity.llm_gateway.LocalLLMGateway", return_value=mock_gateway_instance
                ):
                    result = await server._llm_enrich_findings(
                        {
                            "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
                            "findings": [
                                {
                                    "finding_id": "abc-123-def-456",  # Not a valid UUID
                                    "concern": "security",
                                    "severity": "high",
                                    "file": "test.py",
                                    "line_start": 10,
                                    "line_end": 10,
                                    "summary": "Test finding",
                                    "explanation": "Test explanation",
                                    "confidence": 0.8,
                                }
                            ],
                            "diff": "test diff",
                            "mode": "explain",
                        }
                    )

    assert "error" not in result
    assert "enriched_findings" in result
    assert result["count"] == 1
    # The result should contain the original finding_id
    assert result["enriched_findings"][0]["finding_id"] == "abc-123-def-456"


# ── Test: Audit records are persisted ─────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_enrich_audit_persists_to_configured_store():
    """Test that enrichment audit records go to the configured store, not :memory:."""
    from verdity.mcp_server import MCPServer

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_audit.db")

        # Patch get_settings at the module level where it's imported
        with patch("verdity.config.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.audit_sqlite_path = db_path
            mock_get_settings.return_value = mock_settings

            server = MCPServer()

            # Create a mock result
            mock_result = MagicMock()
            mock_result.finding_id = "123e4567-e89b-12d3-a456-426614174000"
            mock_result.mode.value = "explain"
            mock_result.model = "qwen2.5-7b-instruct"
            mock_result.backend = "ollama"
            mock_result.explanation = "Test explanation"
            mock_result.confidence_adjustment_rationale = "Test rationale"
            mock_result.fix_diff = None
            mock_result.fix_explanation = None
            mock_result.risk_score = None
            mock_result.cwe_ids = None
            mock_result.exploitability = None
            mock_result.blast_radius = None

            with patch("verdity.agents.llm_enricher.LLMEnricherAgent") as mock_enricher_class:
                mock_enricher = AsyncMock()
                mock_enricher.enrich_findings = AsyncMock(return_value=[mock_result])
                mock_enricher_class.return_value = mock_enricher

                with patch("verdity.audit_store.AuditStore") as mock_audit_class:
                    mock_audit = AsyncMock()
                    mock_audit.connect = AsyncMock()
                    mock_audit.close = AsyncMock()
                    mock_audit_class.return_value = mock_audit

                    with patch("verdity.token_economics.TokenEconomicsService"):
                        mock_gateway_instance = MagicMock()
                        mock_gateway_instance.close = AsyncMock()
                        with patch(
                            "verdity.llm_gateway.LocalLLMGateway",
                            return_value=mock_gateway_instance,
                        ):
                            result = await server._llm_enrich_findings(
                                {
                                    "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
                                    "findings": [create_finding_data()],
                                    "diff": "test diff",
                                    "mode": "explain",
                                }
                            )

        # Verify that AuditStore was initialized with the correct db_path
        mock_audit_class.assert_called_once_with(db_path=db_path)

        # Check that enrich_findings was called
        mock_enricher.enrich_findings.assert_called_once()

        # Verify result
        assert "error" not in result
        assert "enriched_findings" in result
        assert result["count"] == 1


# ── Test: Gateway and audit store close on failure ───────────────────────


@pytest.mark.asyncio
async def test_llm_enrich_closes_resources_on_gateway_failure():
    """Test that audit_store.close() and gateway.close() run even on exception."""
    import pytest

    from verdity.mcp_server import MCPServer

    server = MCPServer()

    # Track whether close methods were called
    close_calls = {"audit": 0, "gateway": 0}

    # Create mock objects with close tracking
    mock_audit = AsyncMock()
    mock_audit.connect = AsyncMock()

    async def mock_close():
        close_calls["audit"] += 1

    mock_audit.close = AsyncMock(side_effect=mock_close)

    mock_gateway = MagicMock()

    async def mock_gateway_close():
        close_calls["gateway"] += 1

    mock_gateway.close = AsyncMock(side_effect=mock_gateway_close)

    # Patch the modules where the imports happen (local imports in the function)
    with patch("verdity.agents.llm_enricher.LLMEnricherAgent") as mock_enricher_class:
        mock_enricher = AsyncMock()
        mock_enricher.enrich_findings = AsyncMock(side_effect=RuntimeError("Simulated failure"))
        mock_enricher_class.return_value = mock_enricher

        # These are imported locally in the function, so we patch the source modules
        with patch("verdity.audit_store.AuditStore") as mock_audit_class:

            def audit_factory(*args, **kwargs):
                close_calls["audit"] = 0
                return mock_audit

            mock_audit_class.side_effect = audit_factory

            with patch("verdity.llm_gateway.LocalLLMGateway") as mock_gateway_class:

                def gateway_factory(*args, **kwargs):
                    close_calls["gateway"] = 0
                    return mock_gateway

                mock_gateway_class.side_effect = gateway_factory

                with patch("verdity.config.get_settings"):
                    with patch("verdity.token_economics.TokenEconomicsService"):
                        # The exception should be caught by try/finally in the function
                        with pytest.raises(RuntimeError, match="Simulated failure"):
                            result = await server._llm_enrich_findings(
                                {
                                    "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
                                    "findings": [create_finding_data()],
                                    "diff": "test diff",
                                    "mode": "explain",
                                }
                            )

    # Both close methods should have been called even after exception
    assert close_calls["audit"] == 1, "audit_store.close() should be called"
    assert close_calls["gateway"] == 1, "gateway.close() should be called"


# ── Test: Coverage for lines 1234, 1238-1239, 1279-1316 ─────────────────


@pytest.mark.asyncio
async def test_llm_enrich_valid_uuid_finding_id():
    """Test that valid UUID finding_id is used correctly."""
    from verdity.mcp_server import MCPServer

    server = MCPServer()

    # Create a mock result
    mock_result = MagicMock()
    mock_result.finding_id = "123e4567-e89b-12d3-a456-426614174000"
    mock_result.mode.value = "explain"
    mock_result.model = "qwen2.5-7b-instruct"
    mock_result.backend = "ollama"
    mock_result.explanation = "Test explanation"
    mock_result.confidence_adjustment_rationale = "Test rationale"
    mock_result.fix_diff = None
    mock_result.fix_explanation = None
    mock_result.risk_score = None
    mock_result.cwe_ids = None
    mock_result.exploitability = None
    mock_result.blast_radius = None

    with patch("verdity.agents.llm_enricher.LLMEnricherAgent") as mock_enricher_class:
        mock_enricher = AsyncMock()
        mock_enricher.enrich_findings = AsyncMock(return_value=[mock_result])
        mock_enricher_class.return_value = mock_enricher

        with patch("verdity.audit_store.AuditStore") as mock_audit_class:
            mock_audit = AsyncMock()
            mock_audit.connect = AsyncMock()
            mock_audit.close = AsyncMock()
            mock_audit_class.return_value = mock_audit

            with patch("verdity.token_economics.TokenEconomicsService"):
                mock_gateway_instance = MagicMock()
                mock_gateway_instance.close = AsyncMock()
                with patch(
                    "verdity.llm_gateway.LocalLLMGateway", return_value=mock_gateway_instance
                ):
                    result = await server._llm_enrich_findings(
                        {
                            "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
                            "findings": [
                                {
                                    "finding_id": "123e4567-e89b-12d3-a456-426614174000",  # Valid UUID
                                    "concern": "security",
                                    "severity": "high",
                                    "file": "test.py",
                                    "line_start": 10,
                                    "line_end": 10,
                                    "summary": "Test finding",
                                    "explanation": "Test explanation",
                                    "confidence": 0.8,
                                }
                            ],
                            "diff": "test diff",
                            "mode": "explain",
                        }
                    )

    assert "error" not in result
    assert result["enriched_findings"][0]["finding_id"] == "123e4567-e89b-12d3-a456-426614174000"


@pytest.mark.asyncio
async def test_llm_enrich_all_modes():
    """Test all enrichment modes work correctly."""
    from verdity.mcp_server import MCPServer

    for mode in ["explain", "fix", "risk", "all"]:
        server = MCPServer()

        mock_result = MagicMock()
        mock_result.finding_id = "test-id"
        mock_result.mode.value = mode
        mock_result.model = "test-model"
        mock_result.backend = "test-backend"
        mock_result.explanation = "Test explanation"
        mock_result.confidence_adjustment_rationale = "Test rationale"
        mock_result.fix_diff = "diff"
        mock_result.fix_explanation = "Fix explanation"
        mock_result.risk_score = 0.8
        mock_result.cwe_ids = ["CWE-89"]
        mock_result.exploitability = "medium"
        mock_result.blast_radius = "local"

        with patch("verdity.agents.llm_enricher.LLMEnricherAgent") as mock_enricher_class:
            mock_enricher = AsyncMock()
            mock_enricher.enrich_findings = AsyncMock(return_value=[mock_result])
            mock_enricher_class.return_value = mock_enricher

            with patch("verdity.audit_store.AuditStore") as mock_audit_class:
                mock_audit = AsyncMock()
                mock_audit.connect = AsyncMock()
                mock_audit.close = AsyncMock()
                mock_audit_class.return_value = mock_audit

                with patch("verdity.token_economics.TokenEconomicsService"):
                    mock_gateway_instance = MagicMock()
                    mock_gateway_instance.close = AsyncMock()
                    with patch(
                        "verdity.llm_gateway.LocalLLMGateway", return_value=mock_gateway_instance
                    ):
                        result = await server._llm_enrich_findings(
                            {
                                "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
                                "findings": [create_finding_data()],
                                "diff": "test diff",
                                "mode": mode,
                            }
                        )

        assert "error" not in result
        assert result["enriched_findings"][0]["mode"] == mode


@pytest.mark.asyncio
async def test_llm_enrich_invalid_severity():
    """Test that invalid severity returns structured error."""
    from verdity.mcp_server import MCPServer

    server = MCPServer()
    result = await server._llm_enrich_findings(
        {
            "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
            "findings": [
                {
                    "finding_id": "abc-123",
                    "concern": "security",
                    "severity": "invalid_severity",
                    "file": "test.py",
                    "line_start": 10,
                    "line_end": 10,
                    "summary": "Test finding",
                    "explanation": "Test explanation",
                    "confidence": 0.8,
                }
            ],
            "diff": "test diff",
            "mode": "explain",
        }
    )
    assert "error" in result
    assert "Invalid severity" in result["error"]


@pytest.mark.asyncio
async def test_llm_enrich_invalid_finding_structure():
    """Test that invalid finding structure returns structured error."""
    from verdity.mcp_server import MCPServer

    server = MCPServer()
    # Provide a finding with wrong type (dict instead of list for findings)
    result = await server._llm_enrich_findings(
        {
            "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
            "findings": "not a list",
            "diff": "test diff",
            "mode": "explain",
        }
    )
    assert "error" in result

    # Provide a list containing a non-dict item to trigger TypeError inside the loop
    result2 = await server._llm_enrich_findings(
        {
            "review_run_id": "123e4567-e89b-12d3-a456-426614174000",
            "findings": [None],  # non-dict element triggers TypeError on 'k not in f'
            "diff": "test diff",
            "mode": "explain",
        }
    )
    assert "error" in result2
    assert "Invalid finding structure" in result2["error"]


@pytest.mark.asyncio
async def test_llm_enrich_invalid_review_run_id():
    """Test that invalid review_run_id returns structured error."""
    from verdity.mcp_server import MCPServer

    server = MCPServer()
    result = await server._llm_enrich_findings(
        {
            "review_run_id": "not-a-uuid",
            "findings": [create_finding_data()],
            "diff": "test diff",
            "mode": "explain",
        }
    )
    assert "error" in result
    assert "Invalid review_run_id" in result["error"]
