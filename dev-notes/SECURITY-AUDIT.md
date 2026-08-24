# Security Audit Report — Verdity

**Date:** 2026-08-23  
**Auditor:** Agnes (AI agent, cybersecurity skill)  
**Method:** OWASP Top 10 2021 + OWASP Top 10 for LLM Applications 2025 + STRIDE threat modeling

---

## Executive Summary

Verdity implements strong security controls for an AI-powered PR review system. All nine non-negotiable constraints are enforced in code. This audit identifies **3 medium-severity issues** (all fixed) and **2 low-severity informational items**. No critical vulnerabilities were found.

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 3 | ✅ All fixed |
| Low | 2 | ℹ️ Documented |
| Informational | 1 | ℹ️ Documented |

---

## Findings

### M1 — Delivery-ID Cache Grows Without Bound (Fixed)

**Location:** `gateway/app.py` (original)  
**Severity:** Medium  
**OWASP:** A01:2021 — Broken Access Control; LLMOOP: LLM06 (Excessive Agency)  
**CWE:** CWE-400 (Uncontrolled Resource Consumption)

**Issue:** The original gateway stored delivery IDs in an in-memory `set` with no eviction. Under sustained webhook traffic, this set grows without bound, leading to memory exhaustion (DoS).

**Fix Applied:** Added TTL-based eviction with `_delivery_cache_ts` dict tracking insertion time per ID, and `_cleanup_delivery_cache()` called every 5 minutes. TTL is 24 hours (matching GitHub's delivery retention window).

```python
# Fixed: TTL-based eviction
_DELIVERY_CACHE_TTL_SECONDS = 24 * 3600
_eviction_interval_seconds = 300

def _cleanup_delivery_cache(state):
    now = time.time()
    expired = [k for k, ts in state._delivery_cache_ts.items() if now - ts > _DELIVERY_CACHE_TTL_SECONDS]
    for k in expired:
        state.delivery_ids.discard(k)
        state._delivery_cache_ts.pop(k, None)
```

---

### M2 — No Request Body Size Limit (Fixed)

**Location:** `gateway/app.py` (original)  
**Severity:** Medium  
**OWASP:** A04:2021 — Insecure Design; LLMOOP: LLM06 (Excessive Agency)  
**CWE:** CWE-400 (Uncontrolled Resource Consumption)

**Issue:** The webhook endpoint accepted requests of any size. An attacker could send multi-gigabyte payloads, consuming memory and CPU during JSON parsing before HMAC verification even runs.

**Fix Applied:** Added a middleware that checks `Content-Length` header and rejects payloads exceeding 10 MiB (GitHub's practical limit) with HTTP 413. Also enforces the limit after body read as a defense-in-depth layer.

```python
MAX_WEBHOOK_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        if int(content_length) > MAX_WEBHOOK_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "Payload too large"})
    response = await call_next(request)
    _add_security_headers(response)
    return response
```

---

### M3 — Missing Security Headers (Fixed)

**Location:** `gateway/app.py` (original)  
**Severity:** Medium  
**OWASP:** A05:2021 — Security Misconfiguration  
**CWE:** CWE-693 (Protection Mechanism Failure)

**Issue:** The FastAPI application returned responses without security headers, leaving consumers vulnerable to MIME sniffing, clickjacking, and XSS across other sites.

**Fix Applied:** Added a `security_middleware` that attaches the following headers to every response:

| Header | Value | Purpose |
|--------|-------|---------|
| `X-Content-Type-Options` | `nosniff` | Prevents MIME type sniffing |
| `X-Frame-Options` | `DENY` | Prevents clickjacking |
| `Cache-Control` | `no-store, no-cache, must-revalidate` | Prevents caching of webhook responses |
| `Content-Security-Policy` | `default-src 'none'` | No resources loaded |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` | Forces HTTPS |
| `Referrer-Policy` | `no-referrer` | No referrer leakage |
| `X-XSS-Protection` | `1; mode=block` | Legacy XSS filter |

---

### L4 — Path Traversal in PR References (Fixed)

**Location:** `gateway/app.py`  
**Severity:** Low  
**OWASP:** A01:2021 — Broken Access Control  
**CWE:** CWE-22 (Path Traversal)

**Issue:** The `head_sha` and `base_sha` fields from the GitHub payload are used directly without validation. While they're currently only stored as strings (not passed to filesystem operations), future code that uses them could be vulnerable.

**Fix Applied:** Added `_sanitize_path()` validator that rejects null bytes, absolute paths, and `..` traversal sequences in PR reference fields before they enter the system.

---

### L5 — No Rate Limiting on Webhook Endpoint (Documented)

**Location:** `gateway/app.py`  
**Severity:** Low  
**OWASP:** A04:2021 — Insecure Design  
**CWE:** CWE-770 (Allocation of Resources Without Limits)

**Issue:** The gateway accepts unlimited concurrent webhook requests. In production, a flood of webhooks could overwhelm downstream processing.

**Status:** Not fixed — this is intentionally deferred to the deployment layer (reverse proxy / WAF). The durable queue already absorbs bursts. A production deployment should place a rate limiter (e.g., NGINX `limit_req`, Cloudflare) in front of the gateway.

**Recommendation:** Add rate limiting at the infrastructure level. The gateway's role is verification + enqueue — it should never be the rate-limiting bottleneck.

---

### L6 — Test Secrets in conftest.py (Documented)

**Location:** `tests/conftest.py`  
**Severity:** Low  
**CWE:** CWE-798 (Use of Hard-coded Credentials)

**Issue:** Test fixtures use a weak, hard-coded HMAC secret (`test-hmac-secret-key-for-dev-only`).

**Status:** Not a vulnerability — this is intentionally test-only. The `.env` file is in `.gitignore`. The secret is never used in production. Documented as a known-dev-only pattern.

**Recommendation:** Continue using `setdefault()` to allow test overrides without requiring a `.env` file.

---

### I7 — Prompt Injection Defense is Architectural (Documented)

**Location:** `agents/security.py`, `aggregator.py`, `gateway/app.py`  
**Severity:** Informational  
**OWASP LLMOOP:** LLM01 — Prompt Injection

**Analysis:** Verdity treats all PR diff content as untrusted data, never as instructions. The defense is architectural:

1. **Schema enforcement** — All agent output must conform to Pydantic `Finding` models. An injected prompt cannot change the output structure.
2. **No eval/exec of agent output** — Findings are data objects, never passed to `eval()`, `exec()`, or template engines.
3. **Independent verifier** — The `VerifierSubagent` has no visibility into the `CodingAgent`'s reasoning, preventing self-confirmation bias.
4. **Confidence is deterministic** — `_compute_secret_confidence()` uses rule-based logic, never LLM self-report.

**Recommendation:** When LLM calls are added (future), wrap all external content with the instruction hierarchy pattern from the LLM defense architecture: privileged context (system prompt) separated from untrusted context (PR diffs) using XML delimiters and explicit role labeling.

---

## STRIDE Checklist — Verified Against Implementation

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

---

## Remediation Summary

| Finding | Fix | File |
|---------|-----|------|
| M1: Cache memory leak | TTL-based eviction | `gateway/app.py` |
| M2: No body size limit | Middleware rejects >10 MiB | `gateway/app.py` |
| M3: Missing security headers | `security_middleware` adds 7 headers | `gateway/app.py` |
| L4: Path traversal in refs | `_sanitize_path()` validator | `gateway/app.py` |
| L5: No rate limiting | Deferred to infra layer | Documented |
| L6: Test secrets | Intentional dev-only; documented | `conftest.py` |

---

## Post-Audit Test Results

```
104 passed, 0 failed, 1 warning in 4.36s
Coverage: 100% (enforced via --cov-fail-under=100)
```

All security fixes are covered by existing and new tests. The gateway middleware, cache eviction, path sanitization, and security headers are all tested in `test_phase8.py` and `test_gateway.py`.
