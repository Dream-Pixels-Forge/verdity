"""
Tests for LLM Enricher Agent (Issue #52).

TDD: Tests written first (RED phase), then implementation follows.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from verdity.agents.llm_enricher import (
    EnrichmentMode,
    EnrichmentResult,
    LLMEnricherAgent,
)

# ── Helpers ─────────────────────────────────────────────────────────────


def _sample_findings(concern_type=None):
    """Create sample findings for testing."""
    from verdity.schemas import ConcernType, EvidenceItem, Finding, Severity

    if concern_type is None:
        concern_type = ConcernType.SECURITY

    base_id = uuid.uuid4()
    return [
        Finding(
            finding_id=base_id,
            concern=concern_type,
            severity=Severity.HIGH,
            file="auth/login.py",
            line_start=15,
            line_end=15,
            summary="Potential SQL injection via f-string",
            explanation="User input directly interpolated into SQL query",
            suggested_fix_diff=None,
            confidence=0.85,
            evidence=[
                EvidenceItem(
                    tool="static_analyzer",
                    result="sql_injection",
                    query='f"SELECT',
                )
            ],
            agent_version="security-agent@0.1.0",
            prompt_hash="sha256:abc123",
        ),
        Finding(
            finding_id=uuid.uuid4(),
            concern=concern_type,
            severity=Severity.MEDIUM,
            file="utils/helpers.py",
            line_start=42,
            line_end=42,
            summary="Function too complex (cyclomatic complexity > 10)",
            explanation="Function 'process_data' has 15 branches, consider refactoring",
            suggested_fix_diff=None,
            confidence=0.75,
            evidence=[
                EvidenceItem(
                    tool="complexity_analyzer",
                    result="high_complexity",
                    query="cyclomatic",
                )
            ],
            agent_version="code_quality-agent@0.1.0",
            prompt_hash="sha256:def456",
        ),
        Finding(
            finding_id=uuid.uuid4(),
            concern=concern_type,
            severity=Severity.LOW,
            file="tests/test_auth.py",
            line_start=10,
            line_end=10,
            summary="Missing test coverage for error path",
            explanation="Exception handling branch not covered by tests",
            suggested_fix_diff=None,
            confidence=0.60,
            evidence=[
                EvidenceItem(
                    tool="coverage_analyzer",
                    result="missing_branch",
                    query="except",
                )
            ],
            agent_version="testing-agent@0.1.0",
            prompt_hash="sha256:ghi789",
        ),
    ]


def _sample_findings_mixed():
    """Create sample findings with mixed concern types for batch testing."""
    from verdity.schemas import ConcernType, EvidenceItem, Finding, Severity

    return [
        Finding(
            finding_id=uuid.uuid4(),
            concern=ConcernType.SECURITY,
            severity=Severity.HIGH,
            file="auth/login.py",
            line_start=15,
            line_end=15,
            summary="Potential SQL injection via f-string",
            explanation="User input directly interpolated into SQL query",
            suggested_fix_diff=None,
            confidence=0.85,
            evidence=[
                EvidenceItem(
                    tool="static_analyzer",
                    result="sql_injection",
                    query='f"SELECT',
                )
            ],
            agent_version="security-agent@0.1.0",
            prompt_hash="sha256:abc123",
        ),
        Finding(
            finding_id=uuid.uuid4(),
            concern=ConcernType.CODE_QUALITY,
            severity=Severity.MEDIUM,
            file="utils/helpers.py",
            line_start=42,
            line_end=42,
            summary="Function too complex (cyclomatic complexity > 10)",
            explanation="Function 'process_data' has 15 branches, consider refactoring",
            suggested_fix_diff=None,
            confidence=0.75,
            evidence=[
                EvidenceItem(
                    tool="complexity_analyzer",
                    result="high_complexity",
                    query="cyclomatic",
                )
            ],
            agent_version="code_quality-agent@0.1.0",
            prompt_hash="sha256:def456",
        ),
        Finding(
            finding_id=uuid.uuid4(),
            concern=ConcernType.TESTING,
            severity=Severity.LOW,
            file="tests/test_auth.py",
            line_start=10,
            line_end=10,
            summary="Missing test coverage for error path",
            explanation="Exception handling branch not covered by tests",
            suggested_fix_diff=None,
            confidence=0.60,
            evidence=[
                EvidenceItem(
                    tool="coverage_analyzer",
                    result="missing_branch",
                    query="except",
                )
            ],
            agent_version="testing-agent@0.1.0",
            prompt_hash="sha256:ghi789",
        ),
    ]


def _sample_diff():
    """Create a sample diff for testing."""
    return """--- a/auth/login.py
+++ b/auth/login.py
@@ -10,6 +10,12 @@
 import hashlib
+import uuid

 def login(username, password):
     password_hash = hashlib.md5(password.encode()).hexdigest()
-    user = db.query("SELECT * FROM users WHERE username = ? AND password = ?", username, password_hash)
+    # BUG: Missing token validation — any request with valid username bypasses auth
+    user = db.query("SELECT * FROM users WHERE username = ?", username)
+    if user:
+        token = str(uuid.uuid4())
+        return {"token": token, "user": user}
     return None
--- a/utils/helpers.py
+++ b/utils/helpers.py
@@ -40,6 +40,7 @@
 def process_data(items):
     for item in items:
         if item.is_valid:
+            if item.type == "special":
             result = transform(item)
             save(result)
"""


def _sample_context():
    """Create a sample SpecialistContext for testing."""
    from verdity.schemas import ReviewPolicy, SpecialistContext

    return SpecialistContext(
        review_run_id=uuid.uuid4(),
        repo_owner="test-owner",
        repo_name="test-repo",
        base_sha="abc123",
        head_sha="def456",
        diff_files=[
            {
                "path": "auth/login.py",
                "content": _sample_diff(),
                "additions": _sample_diff(),
            },
            {
                "path": "utils/helpers.py",
                "content": _sample_diff(),
                "additions": _sample_diff(),
            },
        ],
        policy=ReviewPolicy(),
    )


# ── EnrichmentMode Enum Tests ───────────────────────────────────────────


class TestEnrichmentMode:
    """Tests for EnrichmentMode enum."""

    def test_mode_values(self):
        assert EnrichmentMode.EXPLAIN.value == "explain"
        assert EnrichmentMode.FIX.value == "fix"
        assert EnrichmentMode.RISK.value == "risk"
        assert EnrichmentMode.ALL.value == "all"

    def test_mode_from_string(self):
        assert EnrichmentMode("explain") == EnrichmentMode.EXPLAIN
        assert EnrichmentMode("fix") == EnrichmentMode.FIX
        assert EnrichmentMode("risk") == EnrichmentMode.RISK
        assert EnrichmentMode("all") == EnrichmentMode.ALL


# ── EnrichmentResult Dataclass Tests ────────────────────────────────────


class TestEnrichmentResult:
    """Tests for EnrichmentResult dataclass."""

    def test_explain_mode_fields(self):
        finding_id = uuid.uuid4()
        result = EnrichmentResult(
            finding_id=str(finding_id),
            mode=EnrichmentMode.EXPLAIN,
            model="gpt-4o-mini",
            backend="openai",
            explanation="This is a real issue because...",
            confidence_adjustment_rationale="High confidence due to clear pattern match",
        )
        assert result.finding_id == str(finding_id)
        assert result.mode == EnrichmentMode.EXPLAIN
        assert result.explanation is not None
        assert result.confidence_adjustment_rationale is not None
        assert result.fix_diff is None
        assert result.risk_score is None

    def test_fix_mode_fields(self):
        finding_id = uuid.uuid4()
        result = EnrichmentResult(
            finding_id=str(finding_id),
            mode=EnrichmentMode.FIX,
            model="gpt-4o-mini",
            backend="openai",
            fix_diff='- cursor.execute(f"SELECT ...")\n+ cursor.execute("SELECT ... WHERE id = %s", (user_id,))',
            fix_explanation="Use parameterized query to prevent SQL injection",
        )
        assert result.fix_diff is not None
        assert result.fix_explanation is not None
        assert result.explanation is None
        assert result.risk_score is None

    def test_risk_mode_fields(self):
        finding_id = uuid.uuid4()
        result = EnrichmentResult(
            finding_id=str(finding_id),
            mode=EnrichmentMode.RISK,
            model="gpt-4o-mini",
            backend="openai",
            risk_score=0.85,
            cwe_ids=["CWE-89"],
            exploitability="HIGH",
            blast_radius="Database compromise",
        )
        assert result.risk_score == 0.85
        assert result.cwe_ids == ["CWE-89"]
        assert result.exploitability == "HIGH"
        assert result.blast_radius == "Database compromise"
        assert result.explanation is None
        assert result.fix_diff is None


# ── LLMEnricherAgent Tests ─────────────────────────────────────────────


class TestLLMEnricherAgent:
    """Tests for LLMEnricherAgent."""

    def test_init_without_gateway(self):
        """Agent can be initialized without a gateway."""
        agent = LLMEnricherAgent()
        assert agent._gateway is None
        assert agent._config is None

    def test_init_with_config(self):
        """Agent can be initialized with a config."""
        from verdity.llm_gateway import GatewayConfig

        config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
        agent = LLMEnricherAgent(config=config)
        assert agent._config is not None
        assert agent._config.model == "qwen2.5-7b-instruct"

    def test_batch_findings_by_concern(self):
        """Findings are batched by concern type."""
        from verdity.schemas import ConcernType

        findings = _sample_findings_mixed()
        agent = LLMEnricherAgent()
        batches = agent._batch_findings_by_concern(findings)

        assert ConcernType.SECURITY in batches
        assert ConcernType.CODE_QUALITY in batches
        assert ConcernType.TESTING in batches
        assert len(batches[ConcernType.SECURITY]) == 1
        assert len(batches[ConcernType.CODE_QUALITY]) == 1
        assert len(batches[ConcernType.TESTING]) == 1

    def test_format_findings_for_prompt(self):
        """Findings are formatted correctly for prompts."""
        findings = _sample_findings()
        agent = LLMEnricherAgent()
        formatted = agent._format_findings_for_prompt(findings)

        assert "finding_id:" in formatted
        assert "concern: security" in formatted
        assert "severity: high" in formatted
        assert "file: auth/login.py" in formatted
        assert "summary: Potential SQL injection" in formatted

    def test_redact_secrets(self):
        """Secrets are redacted from diff text."""
        agent = LLMEnricherAgent()
        text = 'api_key = "sk-1234567890abcdef1234567890abcdef"'
        redacted = agent._redact_secrets(text)
        assert "sk-1234567890abcdef1234567890abcdef" not in redacted
        assert "[REDACTED:OPENAI_API_KEY]" in redacted

    def test_redact_secrets_aws(self):
        """AWS secrets are redacted."""
        agent = LLMEnricherAgent()
        text = 'AWS_SECRET_ACCESS_KEY = "abcdefghijklmnopqrstuvwxyz1234567890AB"'
        redacted = agent._redact_secrets(text)
        assert "abcdefghijklmnopqrstuvwxyz1234567890AB" not in redacted
        assert "[REDACTED:AWS_SECRET_ACCESS_KEY]" in redacted

    def test_prompt_templates_exist(self):
        """All prompt templates are initialized."""
        agent = LLMEnricherAgent()
        for mode in EnrichmentMode:
            template = agent._get_prompt_template(mode)
            assert template, f"Missing template for {mode}"
            assert "{findings}" in template
            assert "{diff}" in template

    def test_output_schemas_exist(self):
        """All output schemas are initialized."""
        agent = LLMEnricherAgent()
        for mode in EnrichmentMode:
            schema = agent._get_output_schema(mode)
            assert schema, f"Missing schema for {mode}"
            assert "type" in schema
            assert schema["type"] == "array"
            assert "items" in schema
            assert "required" in schema["items"]


class TestLLMEnricherAgentEnrichment:
    """Integration tests for enrichment (mocked gateway)."""

    @pytest.mark.asyncio
    async def test_enrich_findings_returns_empty_without_gateway(self):
        """Enrichment returns empty list when no gateway configured."""
        findings = _sample_findings()
        context = _sample_context()
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.token_economics import TokenEconomicsService

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent()
            results = await agent.enrich_findings(
                findings=findings,
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.EXPLAIN,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert results == []

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_enrich_findings_explain_mode(self):
        """Explain mode produces explanation and confidence rationale."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        findings = _sample_findings()
        context = _sample_context()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Create mock gateway
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
            mock_gateway.complete_structured = AsyncMock(
                return_value=[
                    {
                        "finding_id": str(findings[0].finding_id),
                        "explanation": "SQL injection via f-string interpolation allows attacker to execute arbitrary SQL",
                        "confidence_adjustment_rationale": "Pattern match is exact, confidence appropriate",
                    },
                    {
                        "finding_id": str(findings[1].finding_id),
                        "explanation": "High cyclomatic complexity indicates maintainability risk",
                        "confidence_adjustment_rationale": "Complexity metric is objective, confidence appropriate",
                    },
                ]
            )

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=findings,
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.EXPLAIN,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert len(results) == 2
            for result in results:
                assert result.mode == EnrichmentMode.EXPLAIN
                assert result.explanation is not None
                assert result.confidence_adjustment_rationale is not None
                assert result.model == "qwen2.5-7b-instruct"
                assert result.backend == "ollama"

            # Verify gateway was called
            assert mock_gateway.complete_structured.call_count == 1

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_enrich_findings_fix_mode(self):
        """Fix mode produces fix diff and explanation."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        findings = _sample_findings()
        context = _sample_context()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
            mock_gateway.complete_structured = AsyncMock(
                return_value=[
                    {
                        "finding_id": str(findings[0].finding_id),
                        "fix_diff": '- cursor.execute(f"SELECT * FROM users WHERE name = \'{name}\'")\n+ cursor.execute("SELECT * FROM users WHERE name = %s", (name,))',
                        "fix_explanation": "Use parameterized query to prevent SQL injection",
                    },
                ]
            )

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=findings[:1],
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.FIX,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert len(results) == 1
            assert results[0].mode == EnrichmentMode.FIX
            assert results[0].fix_diff is not None
            assert results[0].fix_explanation is not None
            assert "parameterized query" in results[0].fix_explanation

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_enrich_findings_risk_mode(self):
        """Risk mode produces risk score, CWE IDs, exploitability, blast radius."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        findings = _sample_findings()
        context = _sample_context()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
            mock_gateway.complete_structured = AsyncMock(
                return_value=[
                    {
                        "finding_id": str(findings[0].finding_id),
                        "risk_score": 0.9,
                        "cwe_ids": ["CWE-89"],
                        "exploitability": "CRITICAL",
                        "blast_radius": "Full database compromise, potential RCE",
                    },
                ]
            )

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=findings[:1],
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.RISK,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert len(results) == 1
            assert results[0].mode == EnrichmentMode.RISK
            assert results[0].risk_score == 0.9
            assert results[0].cwe_ids == ["CWE-89"]
            assert results[0].exploitability == "CRITICAL"
            assert results[0].blast_radius == "Full database compromise, potential RCE"

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_enrich_findings_all_mode(self):
        """All mode produces all fields."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        findings = _sample_findings()
        context = _sample_context()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
            mock_gateway.complete_structured = AsyncMock(
                return_value=[
                    {
                        "finding_id": str(findings[0].finding_id),
                        "explanation": "SQL injection explanation",
                        "confidence_adjustment_rationale": "High confidence",
                        "fix_diff": "- bad\n+ good",
                        "fix_explanation": "Fix explanation",
                        "risk_score": 0.85,
                        "cwe_ids": ["CWE-89"],
                        "exploitability": "HIGH",
                        "blast_radius": "Database access",
                    },
                ]
            )

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=findings[:1],
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.ALL,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert len(results) == 1
            result = results[0]
            assert result.mode == EnrichmentMode.ALL
            assert result.explanation is not None
            assert result.confidence_adjustment_rationale is not None
            assert result.fix_diff is not None
            assert result.fix_explanation is not None
            assert result.risk_score is not None
            assert result.cwe_ids is not None
            assert result.exploitability is not None
            assert result.blast_radius is not None

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_enrich_findings_batches_by_concern(self):
        """Findings are batched by concern type, separate calls per batch."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        findings = _sample_findings_mixed()
        context = _sample_context()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
            mock_gateway.complete_structured = AsyncMock(
                return_value=[
                    {
                        "finding_id": "test",
                        "explanation": "test",
                        "confidence_adjustment_rationale": "test",
                    },
                ]
            )

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=findings,
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.EXPLAIN,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            # Should have 3 results (one per finding)
            assert len(results) == 3
            # Should have 3 calls (one per concern type batch)
            assert mock_gateway.complete_structured.call_count == 3

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_enrich_empty_findings_returns_empty(self):
        """Empty findings list returns empty results."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=[],
                diff=_sample_diff(),
                context=_sample_context(),
                mode=EnrichmentMode.EXPLAIN,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert results == []
            mock_gateway.complete_structured.assert_not_called()

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_enrich_handles_gateway_error_gracefully(self):
        """Gateway errors are caught and logged, returns empty list."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        findings = _sample_findings()
        context = _sample_context()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
            mock_gateway.complete_structured = AsyncMock(
                side_effect=RuntimeError("LLM unavailable")
            )

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=findings,
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.EXPLAIN,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert results == []

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_risk_score_clamped_to_valid_range(self):
        """Risk scores outside 0.0-1.0 are clamped."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        findings = _sample_findings()
        context = _sample_context()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
            # Return risk_score > 1.0
            mock_gateway.complete_structured = AsyncMock(
                return_value=[
                    {
                        "finding_id": str(findings[0].finding_id),
                        "risk_score": 1.5,
                        "cwe_ids": ["CWE-89"],
                        "exploitability": "HIGH",
                        "blast_radius": "Test",
                    },
                ]
            )

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=findings[:1],
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.RISK,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert results[0].risk_score == 1.0  # Clamped to max

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


class TestLLMEnricherAgentCoverage:
    """Additional tests to cover uncovered lines in LLMEnricherAgent."""

    @pytest.mark.asyncio
    async def test_enrich_findings_empty_list(self):
        """Empty findings list returns empty list (covers line 324)."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=[],
                diff=_sample_diff(),
                context=_sample_context(),
                mode=EnrichmentMode.EXPLAIN,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert results == []
            mock_gateway.complete_structured.assert_not_called()

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_enrich_findings_creates_gateway_from_config(self):
        """Gateway is created from config when _gateway is None (covers line 329)."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Don't provide gateway, only config
            config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            # Mock LocalLLMGateway constructor
            with patch("verdity.agents.llm_enricher.LocalLLMGateway") as MockGateway:
                mock_gateway = AsyncMock(spec=LocalLLMGateway)
                mock_gateway.config = config
                mock_gateway.complete_structured = AsyncMock(
                    return_value=[
                        {
                            "finding_id": "test-id",
                            "explanation": "Test explanation",
                            "confidence_adjustment_rationale": "Test rationale",
                        }
                    ]
                )
                MockGateway.return_value = mock_gateway

                agent = LLMEnricherAgent(config=config)
                findings = _sample_findings()[:1]
                results = await agent.enrich_findings(
                    findings=findings,
                    diff=_sample_diff(),
                    context=_sample_context(),
                    mode=EnrichmentMode.EXPLAIN,
                    token_economics=token_economics,
                    audit_store=audit_store,
                )

                assert len(results) == 1
                MockGateway.assert_called_once_with(config=config)

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_enrich_findings_no_gateway_no_config_logs_info(self):
        """Info logged when no gateway and no config (covers lines 483-484)."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.token_economics import TokenEconomicsService

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent()  # No gateway, no config
            findings = _sample_findings()[:1]

            # Capture log output
            with patch("verdity.agents.llm_enricher.logger.info") as mock_info:
                results = await agent.enrich_findings(
                    findings=findings,
                    diff=_sample_diff(),
                    context=_sample_context(),
                    mode=EnrichmentMode.EXPLAIN,
                    token_economics=token_economics,
                    audit_store=audit_store,
                )

            assert results == []
            mock_info.assert_called_once()
            assert "LLM not configured" in str(mock_info.call_args)

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_create_enrichment_result_fallback_mode(self):
        """Fallback case in _create_enrichment_result (covers line 451)."""
        agent = LLMEnricherAgent()

        # Create a mode that doesn't match any known mode
        # We can't easily create an invalid enum value, so we test the else branch
        # by directly calling the method with a mode that falls through
        result = agent._create_enrichment_result(
            finding_id="test-id",
            mode="invalid_mode",  # Not a valid EnrichmentMode
            model="test-model",
            backend="test-backend",
            item={},
        )

        assert result.finding_id == "test-id"
        assert result.model == "test-model"
        assert result.backend == "test-backend"
        # All optional fields should be None
        assert result.explanation is None
        assert result.fix_diff is None
        assert result.risk_score is None
        assert result.cwe_ids is None
        assert result.exploitability is None
        assert result.blast_radius is None
        assert result.confidence_adjustment_rationale is None

    @pytest.mark.asyncio
    async def test_enrich_batch_empty_findings(self):
        """_enrich_batch with empty findings returns empty list (covers line 324)."""
        import os
        import tempfile
        import uuid

        from verdity.audit_store import AuditStore
        from verdity.schemas import ReviewPolicy, SpecialistContext
        from verdity.token_economics import TokenEconomicsService

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent()
            context = SpecialistContext(
                review_run_id=uuid.uuid4(),
                repo_owner="test-owner",
                repo_name="test-repo",
                base_sha="abc123",
                head_sha="def456",
                diff_files=[],
                policy=ReviewPolicy(),
            )

            # Call private method directly with empty findings
            results = await agent._enrich_batch(
                findings=[],
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.EXPLAIN,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert results == []

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_enrich_batch_no_gateway_warns(self):
        """_enrich_batch with no gateway logs warning (covers lines 331-332)."""
        import os
        import tempfile
        import uuid

        from verdity.audit_store import AuditStore
        from verdity.schemas import ReviewPolicy, SpecialistContext
        from verdity.token_economics import TokenEconomicsService

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent()  # No gateway, no config
            findings = _sample_findings()[:1]
            context = SpecialistContext(
                review_run_id=uuid.uuid4(),
                repo_owner="test-owner",
                repo_name="test-repo",
                base_sha="abc123",
                head_sha="def456",
                diff_files=[],
                policy=ReviewPolicy(),
            )

            # Capture log output
            with patch("verdity.agents.llm_enricher.logger.warning") as mock_warning:
                results = await agent._enrich_batch(
                    findings=findings,
                    diff=_sample_diff(),
                    context=context,
                    mode=EnrichmentMode.EXPLAIN,
                    token_economics=token_economics,
                    audit_store=audit_store,
                )

            assert results == []
            mock_warning.assert_called_once()
            assert "No LLM gateway configured" in str(mock_warning.call_args)

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


# ── Gate Test ───────────────────────────────────────────────────────────


class TestGateIssue52:
    """Issue #52 gate: LLMEnricherAgent works with mocked gateway."""

    @pytest.mark.asyncio
    async def test_gate_enricher_agent_explain_mode(self):
        """Enricher agent produces explain enrichment with mocked gateway."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        findings = _sample_findings()
        context = _sample_context()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
            mock_gateway.complete_structured = AsyncMock(
                return_value=[
                    {
                        "finding_id": str(f.finding_id),
                        "explanation": f"Explanation for {f.summary}",
                        "confidence_adjustment_rationale": "Rationale",
                    }
                    for f in findings
                ]
            )

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=findings,
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.EXPLAIN,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert len(results) == len(findings)
            for result in results:
                assert result.mode == EnrichmentMode.EXPLAIN
                assert result.explanation is not None
                assert result.confidence_adjustment_rationale is not None

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_gate_enricher_agent_fix_mode(self):
        """Enricher agent produces fix enrichment with mocked gateway."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        findings = _sample_findings()
        context = _sample_context()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
            mock_gateway.complete_structured = AsyncMock(
                return_value=[
                    {
                        "finding_id": str(f.finding_id),
                        "fix_diff": f"- bad\n+ good for {f.summary}",
                        "fix_explanation": "Fix explanation",
                    }
                    for f in findings
                ]
            )

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=findings,
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.FIX,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert len(results) == len(findings)
            for result in results:
                assert result.mode == EnrichmentMode.FIX
                assert result.fix_diff is not None
                assert result.fix_explanation is not None

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_gate_enricher_agent_risk_mode(self):
        """Enricher agent produces risk enrichment with mocked gateway."""
        import os
        import tempfile

        from verdity.audit_store import AuditStore
        from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
        from verdity.token_economics import TokenEconomicsService

        findings = _sample_findings()
        context = _sample_context()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            mock_gateway = AsyncMock(spec=LocalLLMGateway)
            mock_gateway.config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
            mock_gateway.complete_structured = AsyncMock(
                return_value=[
                    {
                        "finding_id": str(f.finding_id),
                        "risk_score": 0.8,
                        "cwe_ids": ["CWE-89"],
                        "exploitability": "HIGH",
                        "blast_radius": "Database",
                    }
                    for f in findings
                ]
            )

            token_economics = TokenEconomicsService()
            audit_store = AuditStore(db_path=db_path)
            await audit_store.connect()

            agent = LLMEnricherAgent(gateway=mock_gateway)
            results = await agent.enrich_findings(
                findings=findings,
                diff=_sample_diff(),
                context=context,
                mode=EnrichmentMode.RISK,
                token_economics=token_economics,
                audit_store=audit_store,
            )

            assert len(results) == len(findings)
            for result in results:
                assert result.mode == EnrichmentMode.RISK
                assert result.risk_score == 0.8
                assert result.cwe_ids == ["CWE-89"]
                assert result.exploitability == "HIGH"
                assert result.blast_radius == "Database"

            await audit_store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
