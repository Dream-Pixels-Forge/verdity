# SG-02 — Fix the `llm_enrich_findings` MCP tool (fails on every call)

**Severity:** High (confirmed) · **Size:** S · **Depends on:** SG-01 · **Branch:** `fix/sg-02-mcp-llm-enrich`

## Goal
Make the v0.5.0 MCP tool `llm_enrich_findings` work for valid input, keep its audit records, and return results that callers can match back to their findings.

## Evidence
- `src/verdity/mcp_server.py`, `_llm_enrich_findings` (around line 1216): builds `SpecialistContext(..., policy=None)`. `SpecialistContext.policy` is `ReviewPolicy` (not Optional) in `schemas/_models.py:113`, so this **always raises `ValidationError`**. Confirmed by probe.
- `audit_store = AuditStore()` defaults to `:memory:`, so every enrichment audit record is thrown away when the call ends. That fails constraint #9.
- `finding_id=uuid.UUID(f["finding_id"]) if "-" in f["finding_id"] else uuid.uuid4()` silently swaps a non-UUID id for a new random one. Callers can't match the results to their findings. A malformed id that contains `-` raises an unhandled `ValueError`.
- `f["concern"]`, `f["file"]` and others are indexed directly, so a missing key raises `KeyError` instead of returning a clean error.
- `audit_store.close()` and `gateway.close()` aren't in `try/finally`, so a failure leaks the connections.
- Coverage: `mcp_server.py` lines 1234, 1238–1239 and 1279–1316 aren't covered by any test.

## Plan (TDD)
1. **Red:** add `tests/test_mcp_llm_enrich.py` with a stubbed `LocalLLMGateway` (no network) and a valid finding plus diff. Assert that the call returns `{"enriched_findings": [...], "count": 1}`. It must fail with the `ValidationError` before the fix.
2. **Red:** tests for a missing required field (structured `{"error": ...}`, not an exception), a non-UUID `finding_id` (kept and echoed back unchanged), and a gateway that raises (both `close()` calls still run).
3. **Green:**
   - Drop `policy=None`, or pass `ReviewPolicy()`.
   - Keep the caller's `finding_id` string. Only generate one when it's absent, and return a mapping if the internal UUID differs.
   - Validate the input up front and return `{"error": ...}` for bad input.
   - Use the audit store from the server's config (`settings.audit_sqlite_path`) or an injected store, not `:memory:`.
   - Wrap enrichment in `try/finally` for `close()`.
4. **Refactor:** share the "dict → `Finding`" parsing with `cli/enrich.py` (see SG-03) in one helper, for example in `agents/llm_enricher.py`.

## Definition of done
- [ ] The tool succeeds for valid input, verified by test.
- [ ] Bad input returns a structured error, never a traceback.
- [ ] Caller finding ids are kept.
- [ ] Audit rows go to the configured store (a test checks the row exists).
- [ ] `mcp_server.py` is back to 100% coverage; lint and format are clean.

## Commit
`fix(mcp): llm_enrich_findings no longer crashes on policy=None; persist audit, keep finding ids`
