# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-08-23

### Added
- **Ingestion Gateway** — FastAPI endpoint for GitHub webhooks with HMAC-SHA256 verification, replay protection, and 10 MiB body limit
- **Event Queue** — Durable SQLite-backed queue with repo partitioning, retry, and dead-letter support
- **Semantic Index** — Shared embeddings + symbol graph with incremental re-indexing
- **Specialist Agents** — Security, code quality, testing, and documentation agents running in parallel
- **Orchestrator** — Scatter-gather workflow with per-agent timeout isolation
- **Confidence Router** — Deterministic multi-signal scoring (never LLM self-report)
- **Approval Queue** — Persistent store for sub-threshold findings awaiting human review
- **Coding Agent** — Rule-based fix generation for security/quality findings
- **Verification Gate** — Compile → lint → no-new-secrets → independent verifier sequence
- **Regression Runner** — Runs affected test scope before marking fixes ready
- **Token Economics Service** — Per-call metering with budget enforcement
- **Budget Enforcer** — Real-time spend monitoring with graceful degradation signals
- **Audit Store** — Append-only log with SHA-256 integrity checksums
- **Worker** — Background dequeuer with exponential backoff and graceful shutdown
- **Security hardening** — 7 defensive headers, path sanitization, dual-secret rotation
- **236 tests** at 100% coverage

### Changed
- None (initial release)

### Removed
- None (initial release)

### Security
- All webhook HMAC verified with `hmac.compare_digest` before any parsing
- Delivery-ID dedupe cache with 24h TTL eviction
- All secrets from environment/KMS via pydantic `SecretStr`
- Schema-enforced output prevents prompt injection
- STRIDE threat model validated against implementation
