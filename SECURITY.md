# Security Guide — Verdity

## ⚠️ Security Notice

This system handles **source code, secrets, and security findings**. Key controls:

- **HMAC-SHA256** verified over raw body with constant-time comparison — no bypass, ever.
- **Dual-secret rotation** support with configurable grace period.
- **Delivery-ID replay cache** with 24h TTL eviction.
- **All secrets** from environment / KMS — never committed (`.env` in `.gitignore`).
- **Append-only audit log** with SHA-256 integrity checksums.
- **Schema-validated output** — agents cannot inject arbitrary commands; findings are structured data.

## Security Controls

| Control | Implementation |
|---------|---------------|
| HMAC verification | `hmac.compare_digest` — constant-time, no bypass |
| Replay protection | Delivery-ID dedupe cache with 24h TTL eviction |
| Secret management | pydantic `SecretStr`; all secrets from env/KMS |
| Audit integrity | SHA-256 checksum per audit record |
| Output validation | All findings pass Pydantic schema validation |
| Tenant isolation | All stores partitioned by `repo_id` |
| Budget enforcement | Real-time spend monitoring with degrade signals |
| Independent verification | `VerifierSubagent` is separate from `CodingAgent` |
| Security headers | HSTS, CSP, X-Frame-Options on all responses |
| Path sanitization | Rejects traversal, absolute paths, null bytes |

## STRIDE Threat Model

| Threat | Mitigation | Status |
|--------|-----------|--------|
| **Spoofing** — forged webhook | HMAC-SHA256 + `hmac.compare_digest` (constant-time) | ✅ |
| **Spoofing** — leaked secret abuse | Dual-secret rotation with grace period | ✅ |
| **Tampering** — replay attack | Delivery-ID dedupe cache with 24h TTL | ✅ |
| **Tampering** — prompt injection in PR | Schema-enforced output; diff treated as data, not instructions | ✅ |
| **Repudiation** — no decision records | Append-only audit log with SHA-256 checksums per record | ✅ |
| **Info Disclosure** — secrets in code | pydantic `SecretStr`; all from env/KMS; `.env` in `.gitignore` | ✅ |
| **Info Disclosure** — cross-tenant leakage | All stores partitioned by `repo_id` | ✅ |
| **DoS** — webhook flood | Queue absorption + body size limit (10 MiB) + TTL cache | ✅ |
| **DoS** — budget drain | Hard budget caps with graceful degradation | ✅ |
| **Elevation** — self-reviewed code change | `VerifierSubagent` is separate from `CodingAgent` | ✅ |

## Known Limitations

| ID | Issue | Severity | Status |
|----|-------|----------|--------|
| L5 | No rate limiting on gateway endpoint | Low | Deferred to infra layer (reverse proxy / WAF) |
| L6 | Test fixtures use weak dev-only HMAC secret | Low | Intentional — test-only, not committed |
| I7 | Prompt injection defense is architectural, not runtime | Info | Covered by schema enforcement; future LLM calls should use XML-delimited prompts |

## Reporting Security Issues

Please report security vulnerabilities via GitHub Security Advisory, not via public issues.

See [dev-notes/SECURITY-AUDIT.md](dev-notes/SECURITY-AUDIT.md) for the full audit report.
