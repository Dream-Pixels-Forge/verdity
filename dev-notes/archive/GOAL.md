# GOAL.md — Verdity LLM Integration for Scoring & Feedback Confidence

## Goal: Integrate Local LLM Models into Verdity for Finding Scoring, Confidence Calibration, and Human-Readable Feedback

### Objective
Add optional LLM-powered enrichment to Verdity's deterministic review pipeline — enabling per-finding confidence adjustment, detailed explanations, fix generation, and risk assessment — while preserving Constraint #5 (deterministic confidence at runtime) through calibration-weight adjustment rather than per-finding LLM calls.

### Context
Verdity currently uses purely deterministic agents (regex, AST, semantic search) with rule-based confidence scores. The TrustCalibrator learns from human feedback to adjust global weights. Users want:
- **Richer feedback**: Why is this a real issue? Context-aware explanations.
- **Better fixes**: Concrete code patches (not TODO comments).
- **Risk assessment**: Exploitability, blast radius, CWE mapping.
- **Confidence calibration**: LLM-assisted analysis of WHY certain finding types have high FP rates, feeding into TrustCalibrator weight adjustments.

**Key constraint**: Per-finding LLM calls violate Constraint #5. Solution: LLM runs as async enrichment (non-blocking) and as calibration assistant (analyzing patterns to adjust global weights).

### Deliverables
- [ ] **LLM Gateway** (`verdity/llm_gateway.py`) — Unified interface for local LLM inference (ollama, llama.cpp, vLLM backends) with structured output via JSON schema
- [ ] **LLM Enricher Agent** (`verdity/agents/llm_enricher.py`) — Async enrichment modes: `explain`, `fix`, `risk`, `all`; batched by concern type; results stored in `llm_enrichments` table
- [ ] **MCP Tool: `llm_enrich_findings`** — Expose enrichment via MCP; params: `review_run_id`, `findings`, `diff`, `model`, `mode`
- [ ] **Enhanced TrustCalibrator** (`verdity/trust_calibration.py`) — `LLMAssistedCalibrator` that uses LLM to analyze drift patterns and suggest rule/weight adjustments
- [ ] **Database Schema Updates** — New tables: `llm_enrichments`, `calibration_llm_suggestions`
- [ ] **CLI Command: `verdity-enrich`** — `verdity-enrich --run-id <id> --mode explain|fix|risk|all --model <name>`
- [ ] **Configuration** — `.verdity.yml` section for LLM: backend, model, modes, batch_size, timeout
- [ ] **Tests** — Unit tests for gateway, enricher, calibrator integration; integration test with mock LLM
- [ ] **Documentation** — Update README with LLM setup, enrichment usage, calibration workflow

### Definition of Done
- [ ] **Gateway works**: `python -c "from verdity.llm_gateway import LocalLLMGateway; g=LocalLLMGateway(); asyncio.run(g.complete('test', schema={}))"` runs without error (with mock)
- [ ] **Enricher produces output**: Given findings + diff, returns structured enrichment with explanations/fixes/risk
- [ ] **MCP tool registered**: `tools/list` shows `llm_enrich_findings` with correct schema
- [ ] **Enrichment stored**: Results persisted to `llm_enrichments` table with review_run_id linkage
- [ ] **CLI works**: `verdity-enrich --run-id <uuid> --mode explain` outputs enrichment JSON
- [ ] **Calibrator enhanced**: `LLMAssistedCalibrator.analyze_drift_patterns()` returns actionable weight adjustments
- [ ] **All existing tests pass**: `pytest tests/ -q` exits 0
- [ ] **New tests pass**: Coverage ≥80% for new modules
- [ ] **No regressions**: Existing determinism constraint preserved (LLM never modifies finding.confidence at runtime)

### Verification Steps
1. **Gateway test**: `python -m pytest tests/test_llm_gateway.py -v` → all pass
2. **Enricher test**: `python -m pytest tests/test_llm_enricher.py -v` → all pass
3. **Calibrator test**: `python -m pytest tests/test_trust_calibration.py -v` → all pass (including LLM-assisted)
4. **MCP integration test**: Start verdity-mcp, call `tools/list`, verify `llm_enrich_findings` present
5. **CLI integration test**: `verdity-enrich --help` shows correct options; run with mock server
6. **Full pipeline test**: Create mock PR diff, run verdity-review with enrichment, verify output
7. **Constraint #5 check**: Grep for `use_llm=True` in agents — must NOT be in security/quality/testing/doc agents
8. **Coverage**: `pytest --cov=verdity --cov-report=term-missing` → new modules ≥80%

### Anti-Drift Rules
- **LLM never touches finding.confidence at runtime**. Confidence adjustments ONLY via TrustCalibrator weight updates (deterministic at review time).
- **Enrichment is async/optional**. PR review returns immediately; enrichment runs in background or on-demand.
- **No new per-finding LLM calls in specialist agents**. All LLM interaction through gateway → enricher/calibrator.
- **Local-first default**. Gateway defaults to ollama localhost; cloud APIs opt-in via config.
- **Batch findings by concern type**. One LLM call per concern type (security, quality, testing, docs) not per finding.
- **Structured output only**. Gateway enforces JSON schema; no free-text parsing.

### Estimated Effort
- **Gateway + Config**: 2-3 hours
- **Enricher Agent**: 3-4 hours
- **MCP Tool + CLI**: 2-3 hours
- **Calibrator Enhancement**: 2-3 hours
- **Tests + Integration**: 3-4 hours
- **Documentation**: 1-2 hours
- **Total**: ~15-19 hours (2-3 days)

---

## Pipeline Phase Mapping

### Phase 0: Bootstrap (Already Done)
- [x] verdity package installed and working
- [x] MCP server functional (via MCPServer class)
- [x] Existing agents: security, quality, testing, documentation
- [x] TrustCalibrator implemented

### Phase 1: Brainstorm (This Goal)
- [x] Architecture vision documented in this GOAL.md
- [x] LLM integration points identified
- [x] Constraint #5 compliance strategy defined

### Phase 2: Governance + Design System
- [ ] Update GOAL.md with verification gates (this document)
- [ ] Create SPEC.md for LLM Gateway API
- [ ] Create SPEC.md for Enricher Agent API
- [ ] Create SECURITY.md for LLM data handling (no code leaves local unless configured)

### Phase 3: Engineer (Implementation)
Implementation order (dependencies respected):
1. **Task 1**: LLM Gateway (`verdity/llm_gateway.py`) — no deps
2. **Task 2**: Database schema migration — no deps
3. **Task 3**: Enricher Agent (`verdity/agents/llm_enricher.py`) — needs 1, 2
4. **Task 4**: MCP Tool registration (`verdity/mcp_server.py`) — needs 3
5. **Task 5**: CLI command (`verdity/cli/enrich.py`) — needs 1, 3
5. **Task 6**: Enhanced TrustCalibrator (`verdity/trust_calibration.py`) — needs 1
6. **Task 7**: Configuration schema (`.verdity.yml`) — needs 1
7. **Task 8**: Unit tests for all new modules
8. **Task 9**: Integration tests (mock LLM)
9. **Task 10**: Documentation updates

### Parallelization Groups
```
Group 1 (sequential): [Task 1] → [Task 2] → [Task 3]
Group 2 (parallel after Task 3): [Task 4], [Task 5], [Task 6]
Group 3 (parallel): [Task 7], [Task 8], [Task 9]
Group 4: [Task 10]
```

### Git Taxonomy
| Issue | Branch | Type |
|-------|--------|------|
| LLM Gateway | `feature/1-llm-gateway` | enhancement |
| DB Schema | `feature/2-db-schema` | enhancement |
| Enricher Agent | `feature/3-llm-enricher` | enhancement |
| MCP Tool | `feature/4-mcp-enrich-tool` | enhancement |
| CLI Command | `feature/5-cli-enrich` | enhancement |
| Calibrator Enhancement | `feature/6-calibrator-llm` | enhancement |
| Config Schema | `feature/7-config-schema` | enhancement |
| Tests | `feature/8-tests` | enhancement |
| Documentation | `feature/9-docs` | enhancement |

### Verification Gates (Incremental)
| Week | Gate | Implementation |
|------|------|----------------|
| 1 | Tests pass | Ensure `pytest` in CI for new modules |
| 2 | Lint passes | Add `ruff check` for new files |
| 3 | Type check passes | Add `mypy` for new modules |
| 4 | Security scan | Add `bandit` + `verdity` scan |
| 5 | Code review | Require 1 reviewer on PRs |
| 6 | Full pipeline | All gates enforced |

---

## Specifications Referenced

### LLM Gateway API (SPEC.md snippet)
```python
class LocalLLMGateway:
    def __init__(
        self,
        model: str = "qwen2.5-7b-instruct",
        backend: str = "ollama",  # ollama | llama.cpp | vllm
        base_url: str = "http://localhost:11434",
        timeout: float = 30.0,
    ):
    
    async def complete(
        self,
        prompt: str,
        schema: dict | None = None,  # JSON schema for structured output
        temperature: float = 0.1,
    ) -> dict:
        """Returns structured dict matching schema, or raw text if no schema."""
    
    async def complete_batch(
        self,
        prompts: list[tuple[str, dict]],  # (prompt, schema) pairs
    ) -> list[dict]:
        """Batch multiple prompts for efficiency."""
```

### Enricher Agent Modes
| Mode | Prompt Focus | Output Schema |
|------|-------------|---------------|
| `explain` | "Why is this a real issue? Context-aware explanation for developer." | `{finding_id, explanation, confidence_adjustment_rationale}` |
| `fix` | "Generate concrete code fix as unified diff." | `{finding_id, fix_diff, fix_explanation}` |
| `risk` | "Assess exploitability, blast radius, CWE mapping." | `{finding_id, risk_score, cwe_ids, exploitability, blast_radius}` |
| `all` | Combined | All above fields |

### TrustCalibrator LLM Integration
```python
class LLMAssistedCalibrator(TrustCalibrator):
    async def analyze_drift_patterns(
        self,
        finding_type: str,
        false_positive_examples: list[dict],
        confirmed_examples: list[dict],
    ) -> list[CalibrationAction]:
        """
        Uses LLM to understand WHY certain patterns produce FPs.
        Returns: rule modifications, weight adjustments (not per-finding scores).
        """
```

### Database Schema
```sql
-- LLM enrichments (async, per-review-run)
CREATE TABLE llm_enrichments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,  -- UUID from Finding.finding_id
    mode TEXT NOT NULL,        -- explain | fix | risk | all
    model TEXT NOT NULL,
    backend TEXT NOT NULL,
    result_json TEXT NOT NULL, -- structured output
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_enrich_run ON llm_enrichments(review_run_id);
CREATE INDEX idx_enrich_finding ON llm_enrichments(finding_id);

-- LLM-assisted calibration suggestions
CREATE TABLE calibration_llm_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_type TEXT NOT NULL,
    suggested_action_json TEXT NOT NULL,
    model TEXT NOT NULL,
    confidence REAL,
    applied BOOLEAN DEFAULT FALSE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

### Configuration (.verdity.yml)
```yaml
llm:
  enabled: true
  default_model: "qwen2.5-7b-instruct"
  default_backend: "ollama"  # ollama | llama.cpp | vllm
  ollama_url: "http://localhost:11434"
  llama_cpp_path: "/path/to/llama.cpp"
  vllm_url: "http://localhost:8000"
  
  enrichment:
    enabled: true
    default_mode: "explain"  # explain | fix | risk | all
    batch_size: 10
    timeout_seconds: 60
    async: true  # non-blocking enrichment
  
  calibration_assist:
    enabled: true
    trigger_on_drift: true
    min_samples: 20
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Enrichment latency (local) | <5s per batch |
| Fix diff applicability | >90% apply cleanly |
| Risk assessment accuracy | >80% match human expert |
| Calibration weight change impact | Reduce FP rate by >20% |
| Test coverage (new code) | ≥80% |
| Zero regressions in existing tests | 100% pass |

---

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| LLM hallucination in fixes | Medium | High | Gateway enforces schema; fixes verified by applying to branch + running tests |
| LLM latency blocks PR review | Low | High | Async enrichment by default; sync only on-demand |
| Constraint #5 violation | Low | Critical | Code review checks: grep for `use_llm=True` in agents |
| Model availability | Medium | Medium | Fallback chain: local → configured cloud → disabled |
| Data privacy | Low | High | Default local-only; cloud opt-in with explicit config |

---

## Open Questions (Resolve Before Implementation)

1. **Default model**: qwen2.5-7b-instruct (good balance) or phi3-mini (faster)?
2. **Enrichment storage**: Separate table (as specified) or extend `audit_log`?
3. **Calibration LLM frequency**: On-demand (when drift detected) or scheduled (nightly)?
4. **Fix validation**: Auto-apply fix to temp branch + run tests before returning?
5. **Token budget**: Track LLM token usage in `token_economics` or separate?

---

## Approval

- [ ] **Architecture Review**: Pipeline orchestrator approves GOAL.md structure
- [ ] **Security Review**: Security architect approves data handling
- [ ] **Implementation Ready**: All specs clear, tasks decomposed, gates defined

*Generated by pipeline-orchestrator following goal-writer protocol*