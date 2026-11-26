# Task: Codebase review for improvement integration

**Project:** verdity — AI code review / verification gate CLI (Python, src/verdity)
**Working dir:** /home/dimona/Dream-Pixels-Forge/Dev/cli/verdity
**Phase:** R (Review)

## User goal
Review the codebase and determine how to integrate new improvements, specifically:
1. **Score confidence strategy** — confidence scoring on review findings (e.g. ML/rule-based confidence per finding, calibrated thresholds, suppression of low-confidence noise)
2. **Security** — hardening, vulnerability handling, finding verification
3. **Enforcement** — gate policies, blocking rules, how findings block/allow merges (verification_gate, adversarial_reviewer)

## Key modules to examine (READ these, don't guess)
- src/verdity/verification_gate.py — gate logic
- src/verdity/aggregator.py — how findings are combined
- src/verdity/trust_calibration.py — existing calibration
- src/verdity/adversarial_reviewer.py — challenge/verification of findings
- src/verdity/review_rules.py — rule engine
- src/verdity/schemas/_models.py — data models (do findings have confidence fields?)
- src/verdity/config.py — config surface
- src/verdity/budget_enforcer.py, src/verdity/approval_queue.py, src/verdity/audit_store.py
- src/verdity/agents/{security,code_quality,testing,documentation}.py — review agents
- src/verdity/gateway/app.py, src/verdity/hmac_verify.py, src/verdity/rate_limiter.py — security surface
- README.md, CHANGELOG.md, dev-notes/ — existing state/decisions
- tests/ — to gauge test coverage of candidate change areas

## Deliverables (return in final message)
1. **Architecture summary**: how a finding flows agent → aggregator → gate → verdict.
2. **Current state of the three areas**:
   - Score confidence: does any confidence field/calibration exist today? Where would a confidence strategy plug in with minimal disruption?
   - Security: existing hardening (HMAC, rate limiting) and gaps.
   - Enforcement: current gate semantics; how findings block merges; what's configurable.
3. **Integration recommendations**: concrete, minimal-diff design for each improvement — which files to touch, what new modules (if any), what schema changes, backwards-compat notes.
4. **Risk & sequencing**: suggested order of work, estimated complexity (S/M/L), and what tests exist/need adding.

## Rules
- Research only. Write NO code.
- Ground every claim in actual file content (cite file:line where relevant).
- Keep recommendations minimal and surgical per the project's existing style.
