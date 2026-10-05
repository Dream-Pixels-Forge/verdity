"""
Tests for LLM Enrichments and Calibration Suggestions tables (Issue #51).

These tables are added to the AuditStore database for storing:
- llm_enrichments: LLM enrichment results for findings
- calibration_llm_suggestions: LLM-suggested calibration actions
"""

from __future__ import annotations

import json
import uuid

import pytest

from verdity.audit_store import AuditStore


class TestLLMEnrichmentsTable:
    """Tests for the llm_enrichments table."""

    @pytest.mark.asyncio
    async def test_llm_enrichments_table_exists_after_connect(self, audit_store: AuditStore):
        """llm_enrichments table should be created on connect()."""
        # Query sqlite_master to check table exists
        rows = await audit_store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_enrichments'"
        )
        assert len(rows) == 1
        assert rows[0]["name"] == "llm_enrichments"

    @pytest.mark.asyncio
    async def test_llm_enrichments_indexes_exist(self, audit_store: AuditStore):
        """Indexes for llm_enrichments should exist."""
        rows = await audit_store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN ('idx_enrich_run', 'idx_enrich_finding')"
        )
        index_names = {row["name"] for row in rows}
        assert "idx_enrich_run" in index_names
        assert "idx_enrich_finding" in index_names

    @pytest.mark.asyncio
    async def test_insert_llm_enrichment(self, audit_store: AuditStore):
        """Should be able to insert an LLM enrichment record."""
        run_id = uuid.uuid4()
        finding_id = "find-001"
        
        await audit_store._conn.execute(
            """
            INSERT INTO llm_enrichments 
                (review_run_id, finding_id, mode, model, backend, result_json, tokens_used, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run_id),
                finding_id,
                "enrich",
                "gpt-4",
                "openai",
                json.dumps({"explanation": "This is a SQL injection", "severity": "high"}),
                1500,
                0.03,
            ),
        )
        await audit_store._conn.commit()

        rows = await audit_store._conn.execute(
            "SELECT * FROM llm_enrichments WHERE review_run_id = ?",
            (str(run_id),),
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["review_run_id"] == str(run_id)
        assert row["finding_id"] == finding_id
        assert row["mode"] == "enrich"
        assert row["model"] == "gpt-4"
        assert row["backend"] == "openai"
        assert row["tokens_used"] == 1500
        assert row["cost_usd"] == 0.03
        assert row["result_json"] == json.dumps({"explanation": "This is a SQL injection", "severity": "high"})
        # created_at should be auto-populated
        assert row["created_at"] is not None

    @pytest.mark.asyncio
    async def test_query_enrichments_by_run(self, audit_store: AuditStore):
        """Should be able to query enrichments by review_run_id."""
        run_id = uuid.uuid4()
        other_run_id = uuid.uuid4()
        
        for i in range(3):
            await audit_store._conn.execute(
                """
                INSERT INTO llm_enrichments 
                    (review_run_id, finding_id, mode, model, backend, result_json, tokens_used, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    f"find-{i}",
                    "enrich",
                    "gpt-4",
                    "openai",
                    json.dumps({"data": i}),
                    1000 + i,
                    0.02,
                ),
            )
        # Insert for different run
        await audit_store._conn.execute(
            """
            INSERT INTO llm_enrichments 
                (review_run_id, finding_id, mode, model, backend, result_json, tokens_used, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(other_run_id),
                "find-other",
                "enrich",
                "gpt-4",
                "openai",
                json.dumps({"data": "other"}),
                500,
                0.01,
            ),
        )
        await audit_store._conn.commit()

        rows = await audit_store._conn.execute(
            "SELECT * FROM llm_enrichments WHERE review_run_id = ? ORDER BY created_at",
            (str(run_id),),
        )
        assert len(rows) == 3
        for i, row in enumerate(rows):
            assert row["finding_id"] == f"find-{i}"

    @pytest.mark.asyncio
    async def test_query_enrichments_by_finding(self, audit_store: AuditStore):
        """Should be able to query enrichments by finding_id."""
        run_id = uuid.uuid4()
        finding_id = "find-common"
        
        await audit_store._conn.execute(
            """
            INSERT INTO llm_enrichments 
                (review_run_id, finding_id, mode, model, backend, result_json, tokens_used, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run_id),
                finding_id,
                "enrich",
                "gpt-4",
                "openai",
                json.dumps({"attempt": 1}),
                1000,
                0.02,
            ),
        )
        await audit_store._conn.execute(
            """
            INSERT INTO llm_enrichments 
                (review_run_id, finding_id, mode, model, backend, result_json, tokens_used, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run_id),
                finding_id,
                "calibrate",
                "gpt-4",
                "openai",
                json.dumps({"attempt": 2}),
                1500,
                0.03,
            ),
        )
        await audit_store._conn.commit()

        rows = await audit_store._conn.execute(
            "SELECT * FROM llm_enrichments WHERE finding_id = ? ORDER BY created_at",
            (finding_id,),
        )
        assert len(rows) == 2
        assert rows[0]["result_json"] == json.dumps({"attempt": 1})
        assert rows[1]["result_json"] == json.dumps({"attempt": 2})

    @pytest.mark.asyncio
    async def test_default_values_for_tokens_and_cost(self, audit_store: AuditStore):
        """tokens_used and cost_usd should default to 0 when not provided."""
        run_id = uuid.uuid4()
        
        await audit_store._conn.execute(
            """
            INSERT INTO llm_enrichments 
                (review_run_id, finding_id, mode, model, backend, result_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(run_id),
                "find-001",
                "enrich",
                "gpt-4",
                "openai",
                json.dumps({"data": "test"}),
            ),
        )
        await audit_store._conn.commit()

        rows = await audit_store._conn.execute(
            "SELECT tokens_used, cost_usd FROM llm_enrichments WHERE review_run_id = ?",
            (str(run_id),),
        )
        assert len(rows) == 1
        assert rows[0]["tokens_used"] == 0
        assert rows[0]["cost_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_created_at_auto_populated(self, audit_store: AuditStore):
        """created_at should be automatically populated with current timestamp."""
        run_id = uuid.uuid4()
        
        await audit_store._conn.execute(
            """
            INSERT INTO llm_enrichments 
                (review_run_id, finding_id, mode, model, backend, result_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(run_id),
                "find-001",
                "enrich",
                "gpt-4",
                "openai",
                json.dumps({"data": "test"}),
            ),
        )
        await audit_store._conn.commit()

        rows = await audit_store._conn.execute(
            "SELECT created_at FROM llm_enrichments WHERE review_run_id = ?",
            (str(run_id),),
        )
        assert len(rows) == 1
        assert rows[0]["created_at"] is not None
        # Verify it's a valid ISO-like timestamp
        assert "T" in rows[0]["created_at"]


class TestCalibrationLLMSuggestionsTable:
    """Tests for the calibration_llm_suggestions table."""

    @pytest.mark.asyncio
    async def test_calibration_llm_suggestions_table_exists_after_connect(self, audit_store: AuditStore):
        """calibration_llm_suggestions table should be created on connect()."""
        rows = await audit_store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='calibration_llm_suggestions'"
        )
        assert len(rows) == 1
        assert rows[0]["name"] == "calibration_llm_suggestions"

    @pytest.mark.asyncio
    async def test_insert_calibration_suggestion(self, audit_store: AuditStore):
        """Should be able to insert a calibration LLM suggestion."""
        await audit_store._conn.execute(
            """
            INSERT INTO calibration_llm_suggestions
                (finding_type, suggested_action_json, model, confidence)
            VALUES (?, ?, ?, ?)
            """,
            (
                "security-hardcoded-credential",
                json.dumps({"action": "increase_weight", "factor": 1.2}),
                "gpt-4",
                0.85,
            ),
        )
        await audit_store._conn.commit()

        rows = await audit_store._conn.execute(
            "SELECT * FROM calibration_llm_suggestions WHERE finding_type = ?",
            ("security-hardcoded-credential",),
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["finding_type"] == "security-hardcoded-credential"
        assert row["model"] == "gpt-4"
        assert row["confidence"] == 0.85
        assert row["suggested_action_json"] == json.dumps({"action": "increase_weight", "factor": 1.2})
        assert row["applied"] == 0  # False default
        assert row["created_at"] is not None

    @pytest.mark.asyncio
    async def test_applied_defaults_to_false(self, audit_store: AuditStore):
        """applied should default to FALSE (0)."""
        await audit_store._conn.execute(
            """
            INSERT INTO calibration_llm_suggestions
                (finding_type, suggested_action_json, model)
            VALUES (?, ?, ?)
            """,
            (
                "test-type",
                json.dumps({"action": "test"}),
                "gpt-4",
            ),
        )
        await audit_store._conn.commit()

        rows = await audit_store._conn.execute(
            "SELECT applied FROM calibration_llm_suggestions WHERE finding_type = ?",
            ("test-type",),
        )
        assert len(rows) == 1
        assert rows[0]["applied"] == 0

    @pytest.mark.asyncio
    async def test_can_update_applied_flag(self, audit_store: AuditStore):
        """Should be able to mark a suggestion as applied."""
        await audit_store._conn.execute(
            """
            INSERT INTO calibration_llm_suggestions
                (finding_type, suggested_action_json, model, confidence)
            VALUES (?, ?, ?, ?)
            """,
            (
                "test-type",
                json.dumps({"action": "test"}),
                "gpt-4",
                0.9,
            ),
        )
        await audit_store._conn.commit()

        # Get the ID
        rows = await audit_store._conn.execute(
            "SELECT id FROM calibration_llm_suggestions WHERE finding_type = ?",
            ("test-type",),
        )
        suggestion_id = rows[0]["id"]

        # Update applied flag
        await audit_store._conn.execute(
            "UPDATE calibration_llm_suggestions SET applied = 1 WHERE id = ?",
            (suggestion_id,),
        )
        await audit_store._conn.commit()

        rows = await audit_store._conn.execute(
            "SELECT applied FROM calibration_llm_suggestions WHERE id = ?",
            (suggestion_id,),
        )
        assert rows[0]["applied"] == 1

    @pytest.mark.asyncio
    async def test_confidence_is_optional(self, audit_store: AuditStore):
        """confidence should be optional (nullable)."""
        await audit_store._conn.execute(
            """
            INSERT INTO calibration_llm_suggestions
                (finding_type, suggested_action_json, model)
            VALUES (?, ?, ?)
            """,
            (
                "test-type",
                json.dumps({"action": "test"}),
                "gpt-4",
            ),
        )
        await audit_store._conn.commit()

        rows = await audit_store._conn.execute(
            "SELECT confidence FROM calibration_llm_suggestions WHERE finding_type = ?",
            ("test-type",),
        )
        assert len(rows) == 1
        # When not provided, it should be NULL
        assert rows[0]["confidence"] is None

    @pytest.mark.asyncio
    async def test_created_at_auto_populated(self, audit_store: AuditStore):
        """created_at should be automatically populated."""
        await audit_store._conn.execute(
            """
            INSERT INTO calibration_llm_suggestions
                (finding_type, suggested_action_json, model)
            VALUES (?, ?, ?)
            """,
            (
                "test-type",
                json.dumps({"action": "test"}),
                "gpt-4",
            ),
        )
        await audit_store._conn.commit()

        rows = await audit_store._conn.execute(
            "SELECT created_at FROM calibration_llm_suggestions WHERE finding_type = ?",
            ("test-type",),
        )
        assert len(rows) == 1
        assert rows[0]["created_at"] is not None
        assert "T" in rows[0]["created_at"]


class TestMigrationIdempotency:
    """Tests to verify migration is idempotent (can run multiple times)."""

    @pytest.mark.asyncio
    async def test_connect_twice_does_not_fail(self):
        """Calling connect() twice should not raise errors."""
        from verdity.audit_store import AuditStore
        import os
        import tempfile
        
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        try:
            store = AuditStore(db_path=db_path)
            await store.connect()
            await store.connect()  # Second connect should not fail
            await store.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    @pytest.mark.asyncio
    async def test_tables_persist_after_reconnect(self):
        """Tables and data should persist after closing and reconnecting."""
        from verdity.audit_store import AuditStore
        import os
        import tempfile
        import json
        
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        try:
            # First connection - create tables and insert data
            store1 = AuditStore(db_path=db_path)
            await store1.connect()
            run_id = uuid.uuid4()
            await store1._conn.execute(
                """
                INSERT INTO llm_enrichments 
                    (review_run_id, finding_id, mode, model, backend, result_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(run_id), "find-1", "enrich", "gpt-4", "openai", json.dumps({"test": "data"})),
            )
            await store1._conn.commit()
            await store1.close()

            # Second connection - verify data persists
            store2 = AuditStore(db_path=db_path)
            await store2.connect()
            rows = await store2._conn.execute(
                "SELECT * FROM llm_enrichments WHERE review_run_id = ?",
                (str(run_id),),
            )
            assert len(rows) == 1
            assert rows[0]["finding_id"] == "find-1"
            await store2.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)