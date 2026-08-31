"""
Tests for Phase 4: Code Quality, Testing, Documentation agents + Aggregator.
"""

from __future__ import annotations

import uuid

import pytest

from verdity.agents.code_quality import CodeQualityAgent
from verdity.agents.documentation import DocumentationAgent
from verdity.agents.testing import TestingAgent
from verdity.aggregator import AggregatorAgent
from verdity.audit_store import AuditStore
from verdity.schemas import (
    AggregatorOutput,
    ConcernType,
    Finding,
    RepoRef,
    ReviewPolicy,
    Severity,
    SpecialistResponse,
)
from verdity.schemas._models import SpecialistContext
from verdity.semantic_index import SemanticIndex
from verdity.token_economics import TokenEconomicsService


@pytest.fixture
async def services():
    audit = AuditStore(db_path=":memory:")
    await audit.connect()
    index = SemanticIndex(db_path=":memory:")
    await index.connect()
    te = TokenEconomicsService(db_path=":memory:")
    await te.connect()
    yield {"audit": audit, "index": index, "token_economics": te}
    await audit.close()
    await index.close()
    await te.close()


@pytest.fixture
def sample_diff_files():
    return [
        {
            "path": "src/app.py",
            "content": "def hello():\n    print('world')\n\ndef login(user, password):\n    if user:\n        pass\n    return True",
            "additions": "def hello():\n    print('world')\n\ndef login(user, password):\n    if user:\n        pass\n    return True\n",
            "deletions": "",
        },
        {
            "path": "tests/test_app.py",
            "content": "def test_hello():\n    assert hello() == None",
            "additions": "",
            "deletions": "",
        },
    ]


class TestCodeQualityAgent:
    @pytest.mark.asyncio
    async def test_detects_code_quality_issues(self, services, sample_diff_files):
        agent = CodeQualityAgent()
        run_id = uuid.uuid4()
        result = await agent.run(
            ctx=SpecialistContext(
                review_run_id=run_id,
                repo_owner="acme",
                repo_name="widgets",
                base_sha="abc",
                head_sha="def",
                diff_files=sample_diff_files,
                policy=ReviewPolicy(),
            ),
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        assert isinstance(result, SpecialistResponse)
        assert result.status == "complete"
        assert isinstance(result.findings, list)
        # Should detect print_statement and bare 'pass' patterns
        concerns = {f.concern for f in result.findings}
        assert ConcernType.CODE_QUALITY in concerns

    @pytest.mark.asyncio
    async def test_produces_schema_valid_findings(self, services):
        agent = CodeQualityAgent()
        diff_files = [
            {
                "path": "x.py",
                "content": "except:\n    pass",
                "additions": "except:\n    pass\n",
                "deletions": "",
            }
        ]
        result = await agent.run(
            ctx=SpecialistContext(
                review_run_id=uuid.uuid4(),
                repo_owner="acme",
                repo_name="w",
                base_sha="",
                head_sha="",
                diff_files=diff_files,
                policy=ReviewPolicy(),
            ),
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        for f in result.findings:
            assert isinstance(f, Finding)
            assert 0.0 <= f.confidence <= 1.0
            assert len(f.evidence) > 0


class TestTestingAgent:
    @pytest.mark.asyncio
    async def test_detects_testing_issues(self, services, sample_diff_files):
        agent = TestingAgent()
        run_id = uuid.uuid4()
        result = await agent.run(
            ctx=SpecialistContext(
                review_run_id=run_id,
                repo_owner="acme",
                repo_name="widgets",
                base_sha="abc",
                head_sha="def",
                diff_files=sample_diff_files,
                policy=ReviewPolicy(),
            ),
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        assert isinstance(result, SpecialistResponse)
        assert result.status == "complete"
        for f in result.findings:
            assert f.concern == ConcernType.TESTING
            assert 0.0 <= f.confidence <= 1.0


class TestDocumentationAgent:
    @pytest.mark.asyncio
    async def test_detects_doc_issues(self, services, sample_diff_files):
        agent = DocumentationAgent()
        run_id = uuid.uuid4()
        result = await agent.run(
            ctx=SpecialistContext(
                review_run_id=run_id,
                repo_owner="acme",
                repo_name="widgets",
                base_sha="abc",
                head_sha="def",
                diff_files=sample_diff_files,
                policy=ReviewPolicy(),
            ),
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        assert isinstance(result, SpecialistResponse)
        assert result.status == "complete"
        for f in result.findings:
            assert f.concern == ConcernType.DOCUMENTATION


class TestAggregatorAgent:
    @pytest.mark.asyncio
    async def test_deduplicates_overlapping_findings(self):
        agent = AggregatorAgent()
        run_id = uuid.uuid4()
        repo = RepoRef(owner="acme", name="widgets", id=1)

        # Two findings on the same file:line from different specialists
        responses = [
            SpecialistResponse(
                review_run_id=run_id,
                specialist="security",
                status="complete",
                findings=[
                    Finding(
                        concern=ConcernType.SECURITY,
                        severity=Severity.HIGH,
                        file="src/auth.py",
                        line_start=10,
                        line_end=10,
                        summary="Hardcoded password",
                        explanation="password found",
                        confidence=0.85,
                        evidence=[],
                        agent_version="security-agent@0.1.0",
                        prompt_hash="abc",
                    )
                ],
            ),
            SpecialistResponse(
                review_run_id=run_id,
                specialist="code_quality",
                status="complete",
                findings=[
                    Finding(
                        concern=ConcernType.CODE_QUALITY,
                        severity=Severity.LOW,
                        file="src/auth.py",
                        line_start=10,
                        line_end=10,
                        summary="Magic number",
                        explanation="magic number",
                        confidence=0.75,
                        evidence=[],
                        agent_version="code-quality-agent@0.1.0",
                        prompt_hash="def",
                    )
                ],
            ),
        ]

        output = agent.aggregate(run_id, repo, responses)
        assert isinstance(output, AggregatorOutput)
        # Same file+line+different concern → NOT deduped (different concern keys)
        assert len(output.ranked_findings) == 2
        # Both should be present, ranked by severity × confidence
        scores = [r.rank_score for r in output.ranked_findings]
        assert scores[0] >= scores[1]  # highest first

    @pytest.mark.asyncio
    async def test_deduplicates_same_concern(self):
        agent = AggregatorAgent()
        run_id = uuid.uuid4()
        repo = RepoRef(owner="acme", name="w", id=1)

        responses = [
            SpecialistResponse(
                review_run_id=run_id,
                specialist="security",
                status="complete",
                findings=[
                    Finding(
                        concern=ConcernType.SECURITY,
                        severity=Severity.HIGH,
                        file="src/x.py",
                        line_start=5,
                        line_end=5,
                        summary="Secret A",
                        explanation="e1",
                        confidence=0.9,
                        evidence=[],
                        agent_version="v1",
                        prompt_hash="a",
                    )
                ],
            ),
            SpecialistResponse(
                review_run_id=run_id,
                specialist="security",
                status="complete",
                findings=[
                    Finding(
                        concern=ConcernType.SECURITY,
                        severity=Severity.MEDIUM,
                        file="src/x.py",
                        line_start=5,
                        line_end=5,
                        summary="Secret B",
                        explanation="e2",
                        confidence=0.7,
                        evidence=[],
                        agent_version="v1",
                        prompt_hash="b",
                    )
                ],
            ),
        ]

        output = agent.aggregate(run_id, repo, responses)
        # Same (file, line, concern) → deduped to one
        assert len(output.ranked_findings) == 1
        # Best (highest severity × confidence) wins
        assert output.ranked_findings[0].finding.confidence == 0.9

    @pytest.mark.asyncio
    async def test_summary_comment_generated(self):
        agent = AggregatorAgent()
        run_id = uuid.uuid4()
        repo = RepoRef(owner="acme", name="w", id=1)
        responses = [
            SpecialistResponse(
                review_run_id=run_id,
                specialist="security",
                status="complete",
                findings=[
                    Finding(
                        concern=ConcernType.SECURITY,
                        severity=Severity.CRITICAL,
                        file="src/x.py",
                        line_start=1,
                        line_end=1,
                        summary="SQL injection",
                        explanation="bad",
                        confidence=0.95,
                        evidence=[],
                        agent_version="v1",
                        prompt_hash="a",
                    )
                ],
            ),
        ]
        output = agent.aggregate(run_id, repo, responses)
        assert "Verdity Review" in output.summary_comment_markdown
        assert "SQL injection" in output.summary_comment_markdown
        assert "🔴" in output.summary_comment_markdown  # critical emoji


class TestCodeQualityRegex:
    @pytest.mark.asyncio
    async def test_regex_pattern_match(self, services):
        """Test that regex patterns (re: prefix) are matched correctly."""
        agent = CodeQualityAgent()
        # Create a diff with a long function that matches the regex pattern
        # The pattern is: re:def .*\\(.*\\):\\n.{200,}
        long_body = "x" * 250  # 250 characters > 200 threshold
        diff_files = [
            {
                "path": "x.py",
                "content": f"def long_func():\n{long_body}",
                "additions": f"def long_func():\n{long_body}",
                "deletions": "",
            }
        ]
        result = await agent.run(
            ctx=SpecialistContext(
                review_run_id=uuid.uuid4(),
                repo_owner="acme",
                repo_name="w",
                base_sha="",
                head_sha="",
                diff_files=diff_files,
                policy=ReviewPolicy(),
            ),
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        # Should find a long_function issue via regex
        summaries = [f.summary for f in result.findings]
        assert any("long" in s.lower() or "refactor" in s.lower() for s in summaries)
