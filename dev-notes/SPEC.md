# SPEC.md — LLM Integration Architecture Specifications

## 1. LLM Gateway Specification

### 1.1 Interface Contract

```python
# verdity/llm_gateway.py

from abc import ABC, abstractmethod
from typing import Optional, Any
from dataclasses import dataclass

@dataclass
class GatewayConfig:
    model: str = "qwen2.5-7b-instruct"
    backend: str = "ollama"  # ollama | llama.cpp | vllm
    base_url: str = "http://localhost:11434"
    timeout: float = 30.0
    max_retries: int = 2

class LocalLLMGateway:
    """
    Unified interface for local LLM inference backends.
    
    Responsibilities:
    - Backend abstraction (ollama, llama.cpp, vLLM)
    - Structured output via JSON schema
    - Request batching for efficiency
    - Token usage tracking
    - Error handling with fallback
    """
    
    def __init__(self, config: GatewayConfig):
        self.config = config
        self._client = self._create_client()
    
    def _create_client(self) -> "LLMClient":
        if self.config.backend == "ollama":
            from .ollama_client import OllamaClient
            return OllamaClient(self.config)
        elif self.config.backend == "llama.cpp":
            from .llamacpp_client import LlamaCppClient
            return LlamaCppClient(self.config)
        elif self.config.backend == "vllm":
            from .vllm_client import VLLMClient
            return VLLMClient(self.config)
        else:
            raise ValueError(f"Unknown backend: {self.config.backend}")
    
    async def complete(
        self,
        prompt: str,
        schema: Optional[dict] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> dict:
        """
        Single completion with optional structured output.
        
        Args:
            prompt: The input prompt
            schema: JSON Schema for structured output (optional)
            temperature: Sampling temperature
            max_tokens: Maximum output tokens
            
        Returns:
            Dict matching schema, or {"text": "..."} if no schema
        """
        return await self._client.complete(prompt, schema, temperature, max_tokens)
    
    async def complete_batch(
        self,
        requests: list[tuple[str, Optional[dict]]],  # (prompt, schema) pairs
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> list[dict]:
        """
        Batch multiple completions for efficiency.
        
        Returns:
            List of results in same order as requests
        """
        return await self._client.complete_batch(requests, temperature, max_tokens)
    
    async def health_check(self) -> bool:
        """Verify backend is reachable and model loaded."""
        return await self._client.health_check()
```

### 1.2 Backend Implementations

#### Ollama Client
```python
# verdity/ollama_client.py
import httpx
import json

class OllamaClient:
    def __init__(self, config: GatewayConfig):
        self.config = config
        self._client = httpx.AsyncClient(timeout=config.timeout)
    
    async def complete(self, prompt, schema, temperature, max_tokens):
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "temperature": temperature,
            "num_predict": max_tokens,
            "stream": False,
        }
        if schema:
            payload["format"] = schema  # Ollama native structured output
        
        resp = await self._client.post(f"{self.config.base_url}/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return json.loads(data["response"]) if schema else {"text": data["response"]}
    
    async def complete_batch(self, requests, temperature, max_tokens):
        # Ollama doesn't support native batch; sequential with connection pooling
        return [await self.complete(p, s, temperature, max_tokens) for p, s in requests]
    
    async def health_check(self):
        try:
            resp = await self._client.get(f"{self.config.base_url}/api/tags")
            return resp.status_code == 200
        except:
            return False
```

#### llama.cpp Client
```python
# verdity/llamacpp_client.py
import subprocess
import json

class LlamaCppClient:
    def __init__(self, config: GatewayConfig):
        self.config = config
        # Assumes llama-server running at base_url
    
    async def complete(self, prompt, schema, temperature, max_tokens):
        import httpx
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            payload = {
                "prompt": prompt,
                "temperature": temperature,
                "n_predict": max_tokens,
                "json_schema": schema if schema else None,
            }
            resp = await client.post(f"{self.config.base_url}/completion", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return json.loads(data["content"]) if schema else {"text": data["content"]}
    
    async def complete_batch(self, requests, temperature, max_tokens):
        return [await self.complete(p, s, temperature, max_tokens) for p, s in requests]
    
    async def health_check(self):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.config.base_url}/health")
                return resp.status_code == 200
        except:
            return False
```

#### vLLM Client (OpenAI-compatible)
```python
# verdity/vllm_client.py
from openai import AsyncOpenAI

class VLLMClient:
    def __init__(self, config: GatewayConfig):
        self.config = config
        self._client = AsyncOpenAI(
            base_url=f"{config.base_url}/v1",
            api_key="dummy",  # vLLM doesn't require auth by default
        )
    
    async def complete(self, prompt, schema, temperature, max_tokens):
        kwargs = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if schema:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": schema}
        
        resp = await self._client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content
        return json.loads(content) if schema else {"text": content}
    
    async def complete_batch(self, requests, temperature, max_tokens):
        # vLLM supports concurrent requests natively
        import asyncio
        tasks = [self.complete(p, s, temperature, max_tokens) for p, s in requests]
        return await asyncio.gather(*tasks)
    
    async def health_check(self):
        try:
            resp = await self._client.models.list()
            return len(resp.data) > 0
        except:
            return False
```

---

## 2. Enricher Agent Specification

### 2.1 Interface Contract

```python
# verdity/agents/llm_enricher.py

from verdity.schemas import Finding, SpecialistContext
from verdity.token_economics import TokenEconomicsService
from verdity.audit_store import AuditStore
from verdity.llm_gateway import LocalLLMGateway, GatewayConfig
from typing import Optional
from dataclasses import dataclass
from enum import Enum

class EnrichmentMode(Enum):
    EXPLAIN = "explain"
    FIX = "fix"
    RISK = "risk"
    ALL = "all"

@dataclass
class EnrichmentResult:
    finding_id: str
    mode: EnrichmentMode
    model: str
    backend: str
    explanation: Optional[str] = None
    fix_diff: Optional[str] = None
    fix_explanation: Optional[str] = None
    risk_score: Optional[float] = None
    cwe_ids: Optional[list[str]] = None
    exploitability: Optional[str] = None
    blast_radius: Optional[str] = None
    confidence_adjustment_rationale: Optional[str] = None
    tokens_used: dict = None
    cost_usd: float = 0.0

class LLMEnricherAgent:
    """
    Enriches findings with LLM-generated context.
    
    Modes:
    - explain: Why is this a real issue? Context-aware explanation.
    - fix: Generate concrete code fix as unified diff.
    - risk: Assess exploitability, blast radius, CWE mapping.
    - all: All of the above.
    
    Key Properties:
    - Batches findings by concern type (one LLM call per type)
    - Async by default (non-blocking)
    - Results stored in llm_enrichments table
    - Never modifies finding.confidence (Constraint #5)
    """
    
    MODE_PROMPTS = {
        EnrichmentMode.EXPLAIN: """
You are a senior security engineer reviewing code. For the finding below, provide a clear explanation of WHY this is a real issue, with context from the diff. Focus on the specific code pattern, not generic advice.

Finding: {finding_summary}
File: {file_path}:{line_start}
Severity: {severity}
Confidence: {confidence}
Diff Context:
{diff_context}

Output JSON: {{"explanation": "...", "confidence_adjustment_rationale": "..."}}
""",
        EnrichmentMode.FIX: """
You are a senior engineer fixing a code issue. Generate a concrete unified diff patch that fixes the finding. The patch must be minimal and directly address the issue.

Finding: {finding_summary}
File: {file_path}:{line_start}
Severity: {severity}
Current Code Context:
{diff_context}

Output JSON: {{"fix_diff": "...", "fix_explanation": "..."}}
""",
        EnrichmentMode.RISK: """
You are a security architect assessing risk. For this finding, evaluate: exploitability, blast radius, and applicable CWE IDs.

Finding: {finding_summary}
File: {file_path}:{line_start}
Severity: {severity}
Code Context:
{diff_context}

Output JSON: {{"risk_score": 0.0-1.0, "cwe_ids": ["CWE-XXX"], "exploitability": "low|medium|high|critical", "blast_radius": "..."}}
""",
    }
    
    def __init__(
        self,
        gateway: LocalLLMGateway,
        token_economics: TokenEconomicsService,
        audit_store: AuditStore,
        default_mode: EnrichmentMode = EnrichmentMode.EXPLAIN,
        batch_size: int = 10,
    ):
        self.gateway = gateway
        self.token_economics = token_economics
        self.audit_store = audit_store
        self.default_mode = default_mode
        self.batch_size = batch_size
    
    async def enrich_findings(
        self,
        findings: list[Finding],
        diff: str,
        context: SpecialistContext,
        mode: Optional[EnrichmentMode] = None,
    ) -> list[EnrichmentResult]:
        """
        Enrich findings with LLM analysis.
        
        Batches by concern type for targeted prompts.
        Runs async — does not block review pipeline.
        """
        mode = mode or self.default_mode
        results = []
        
        # Group findings by concern type for targeted prompts
        by_concern: dict[str, list[Finding]] = {}
        for f in findings:
            by_concern.setdefault(f.concern.value, []).append(f)
        
        for concern, concern_findings in by_concern.items():
            # Process in batches
            for i in range(0, len(concern_findings), self.batch_size):
                batch = concern_findings[i:i + self.batch_size]
                batch_results = await self._enrich_batch(batch, diff, context, mode, concern)
                results.extend(batch_results)
        
        return results
    
    async def _enrich_batch(
        self,
        findings: list[Finding],
        diff: str,
        context: SpecialistContext,
        mode: EnrichmentMode,
        concern: str,
    ) -> list[EnrichmentResult]:
        # Build prompts for each finding in batch
        # Use structured output schema matching EnrichmentResult fields
        pass
```

### 2.2 Output Schemas

```json
{
  "explain": {
    "type": "object",
    "properties": {
      "explanation": {"type": "string"},
      "confidence_adjustment_rationale": {"type": "string"}
    },
    "required": ["explanation"]
  },
  "fix": {
    "type": "object",
    "properties": {
      "fix_diff": {"type": "string"},
      "fix_explanation": {"type": "string"}
    },
    "required": ["fix_diff"]
  },
  "risk": {
    "type": "object",
    "properties": {
      "risk_score": {"type": "number", "minimum": 0, "maximum": 1},
      "cwe_ids": {"type": "array", "items": {"type": "string", "pattern": "^CWE-\\d+$"}},
      "exploitability": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
      "blast_radius": {"type": "string"}
    },
    "required": ["risk_score", "cwe_ids", "exploitability", "blast_radius"]
  }
}
```

---

## 3. MCP Tool Specification

```python
# In verdity/mcp_server.py — add to _build_tool_definitions()

{
    "name": "llm_enrich_findings",
    "description": "Enrich findings with LLM analysis (explanations, fixes, risk assessment). Runs async by default.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "review_run_id": {"type": "string", "description": "Review run UUID"},
            "findings": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Findings to enrich (from review results)"
            },
            "diff": {"type": "string", "description": "Original code diff"},
            "model": {"type": "string", "description": "LLM model name (optional, uses default)"},
            "backend": {"type": "string", "enum": ["ollama", "llama.cpp", "vllm"], "description": "LLM backend"},
            "mode": {"type": "string", "enum": ["explain", "fix", "risk", "all"], "default": "explain"},
            "async": {"type": "boolean", "default": true, "description": "Run enrichment asynchronously"}
        },
        "required": ["review_run_id", "findings", "diff"]
    }
}
```

---

## 4. CLI Command Specification

```python
# verdity/cli/enrich.py
import click
from verdity.llm_gateway import LocalLLMGateway, GatewayConfig
from verdity.agents.llm_enricher import LLMEnricherAgent, EnrichmentMode
from verdity.token_economics import TokenEconomicsService
from verdity.audit_store import AuditStore

@click.command()
@click.option("--run-id", required=True, help="Review run UUID")
@click.option("--mode", type=click.Choice(["explain", "fix", "risk", "all"]), default="explain")
@click.option("--model", help="LLM model name (overrides config)")
@click.option("--backend", type=click.Choice(["ollama", "llama.cpp", "vllm"]), help="LLM backend")
@click.option("--async/--sync", default=True, help="Run enrichment asynchronously")
@click.option("--output", type=click.Choice(["json", "text"]), default="json")
def enrich(run_id, mode, model, backend, async_, output):
    """Enrich findings from a review run with LLM analysis."""
    pass
```

---

## 5. Enhanced TrustCalibrator Specification

```python
# verdity/trust_calibration.py — additions

class LLMAssistedCalibrator(TrustCalibrator):
    """
    Extends TrustCalibrator with LLM-assisted pattern analysis.
    
    When drift is detected or on schedule, uses LLM to analyze:
    - WHY certain finding types have high false positive rates
    - WHAT rule modifications would reduce false positives
    - WHICH severity/concern weights need adjustment
    
    Returns CalibrationAction objects (weight adjustments, rule changes)
    NOT per-finding scores (preserves Constraint #5).
    """
    
    def __init__(
        self,
        db_path: str = ":memory:",
        metrics_store: object = None,
        llm_gateway: LocalLLMGateway = None,
    ):
        super().__init__(db_path, metrics_store)
        self.llm_gateway = llm_gateway
    
    async def analyze_drift_patterns(
        self,
        finding_type: str,
        false_positive_examples: list[dict],
        confirmed_examples: list[dict],
    ) -> list["CalibrationAction"]:
        """
        Use LLM to understand drift patterns and suggest calibration actions.
        
        Returns list of CalibrationAction:
        - weight_adjustment: modify SEVERITY_WEIGHTS or CONCERN_BOOST
        - rule_modification: add/modify/remove enforcement rules
        - threshold_change: adjust confidence_threshold for finding_type
        """
        if not self.llm_gateway:
            return []
        
        prompt = f"""
        Finding type: {finding_type}
        False positive examples: {len(false_positive_examples)}
        Confirmed examples: {len(confirmed_examples)}
        
        Recent false positives:
        {json.dumps(false_positive_examples[:5], indent=2)}
        
        Recent confirmed:
        {json.dumps(confirmed_examples[:5], indent=2)}
        
        Analyze WHY this finding type has high FP rate.
        Suggest specific calibration actions:
        1. Weight adjustments (severity/concern)
        2. Rule modifications
        3. Threshold changes
        
        Output JSON array of actions.
        """
        
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_type": {"type": "string", "enum": ["weight_adjustment", "rule_modification", "threshold_change"]},
                    "target": {"type": "string"},
                    "current_value": {"type": "number"},
                    "suggested_value": {"type": "number"},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1}
                },
                "required": ["action_type", "target", "rationale"]
            }
        }
        
        result = await self.llm_gateway.complete(prompt, schema, temperature=0.1)
        return [CalibrationAction(**a) for a in result]
    
    async def auto_calibrate_on_drift(self) -> list["CalibrationAction"]:
        """Check for drift and auto-calibrate using LLM analysis."""
        # Implementation
        pass
```

---

## 6. Configuration Schema

```yaml
# .verdity.yml — LLM section
llm:
  enabled: true
  default_model: "qwen2.5-7b-instruct"
  default_backend: "ollama"
  ollama_url: "http://localhost:11434"
  llama_cpp_path: null
  vllm_url: "http://localhost:8000"
  
  enrichment:
    enabled: true
    default_mode: "explain"
    batch_size: 10
    timeout_seconds: 60
    async: true
    store_results: true
  
  calibration_assist:
    enabled: true
    trigger_on_drift: true
    min_samples: 20
    max_suggestions_per_run: 5
  
  models:
    # Model-specific overrides
    "qwen2.5-7b-instruct":
      temperature: 0.1
      max_tokens: 4096
    "phi3-mini":
      temperature: 0.1
      max_tokens: 2048
```

---

## 7. API Contracts (for testing)

### Enrichment API
```python
# POST /api/v1/enrich (if HTTP API added)
{
    "review_run_id": "uuid",
    "findings": [...],
    "diff": "...",
    "mode": "explain|fix|risk|all",
    "model": "optional",
    "async": true
}

# Response (sync)
{
    "enrichments": [
        {"finding_id": "...", "mode": "explain", "explanation": "...", ...}
    ]
}

# Response (async)
{
    "task_id": "uuid",
    "status": "pending",
    "poll_url": "/api/v1/enrich/tasks/{task_id}"
}
```

### Calibration API
```python
# POST /api/v1/calibrate/analyze
{
    "finding_type": "security-hardcoded-credential",
    "false_positive_examples": [...],
    "confirmed_examples": [...]
}

# Response
{
    "actions": [
        {
            "action_type": "weight_adjustment",
            "target": "SEVERITY_WEIGHTS.high",
            "current_value": 4,
            "suggested_value": 3.5,
            "rationale": "...",
            "confidence": 0.85
        }
    ]
}
```

---

*Generated by pipeline-orchestrator for Phase 2 Governance*