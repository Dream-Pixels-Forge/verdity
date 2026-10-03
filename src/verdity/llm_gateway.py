"""
LLM Gateway — unified interface for local LLM inference.

Supports multiple backends:
- Ollama (native API with structured output via format parameter)
- vLLM (OpenAI-compatible API with response_format)
- llama.cpp server (OpenAI-compatible API)

All backends implement the same interface for complete(), complete_batch(), health_check().
Structured output via JSON schema enforcement.
Token usage tracking via TokenEconomicsService.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import yaml

from verdity.token_economics import TokenEconomicsService, estimate_cost

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────────


@dataclass
class GatewayConfig:
    """Configuration for LocalLLMGateway."""

    model: str
    backend: str  # "ollama", "vllm", "llamacpp"
    base_url: str = ""
    timeout: float = 30.0
    max_retries: int = 2
    temperature: float = 0.0
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        # Validate backend
        valid_backends = {"ollama", "vllm", "llamacpp"}
        if self.backend not in valid_backends:
            raise ValueError(
                f"Invalid backend: {self.backend}. Must be one of {valid_backends}"
            )

        # Set default base_url per backend
        if not self.base_url:
            defaults = {
                "ollama": "http://localhost:11434",
                "vllm": "http://localhost:8000",
                "llamacpp": "http://localhost:8080",
            }
            self.base_url = defaults[self.backend]

        # Environment variable overrides
        self.model = os.getenv("VERDITY_LLM_MODEL", self.model)
        self.backend = os.getenv("VERDITY_LLM_BACKEND", self.backend)
        self.base_url = os.getenv("VERDITY_LLM_BASE_URL", self.base_url)
        if os.getenv("VERDITY_LLM_TIMEOUT"):
            self.timeout = float(os.getenv("VERDITY_LLM_TIMEOUT", self.timeout))
        if os.getenv("VERDITY_LLM_MAX_RETRIES"):
            self.max_retries = int(os.getenv("VERDITY_LLM_MAX_RETRIES", self.max_retries))
        if os.getenv("VERDITY_LLM_TEMPERATURE"):
            self.temperature = float(os.getenv("VERDITY_LLM_TEMPERATURE", self.temperature))
        if os.getenv("VERDITY_LLM_MAX_TOKENS"):
            self.max_tokens = int(os.getenv("VERDITY_LLM_MAX_TOKENS", self.max_tokens))


# ── Response ────────────────────────────────────────────────────────────


@dataclass
class LLMGatewayResponse:
    """Structured response from an LLM gateway call."""

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float
    backend: str


# ── JSON Schema Helpers ─────────────────────────────────────────────────


def _extract_json_from_text(text: str) -> str:
    """Extract JSON from text that may contain markdown code fences."""
    # Try to find JSON in code blocks
    block_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if block_match:
        return block_match.group(1).strip()
    # Try to find raw JSON object
    obj_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if obj_match:
        return obj_match.group(0)
    return text.strip()


def _validate_json_against_schema(data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """
    Lightweight JSON schema validation (no external dependency).
    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for field_name in required:
        if field_name not in data:
            errors.append(f"Missing required field: {field_name}")

    for field_name, value in data.items():
        if field_name in properties:
            expected_type = properties[field_name].get("type")
            if expected_type == "string" and not isinstance(value, str):
                errors.append(
                    f"Field '{field_name}' should be string, got {type(value).__name__}"
                )
            elif expected_type == "number" and not isinstance(value, int | float):
                errors.append(
                    f"Field '{field_name}' should be number, got {type(value).__name__}"
                )
            elif expected_type == "boolean" and not isinstance(value, bool):
                errors.append(
                    f"Field '{field_name}' should be boolean, got {type(value).__name__}"
                )
            elif expected_type == "integer" and not isinstance(value, int):
                errors.append(
                    f"Field '{field_name}' should be integer, got {type(value).__name__}"
                )
            elif expected_type == "array" and not isinstance(value, list):
                errors.append(
                    f"Field '{field_name}' should be array, got {type(value).__name__}"
                )

    return errors


# ── Base Client Interface ───────────────────────────────────────────────


class BaseLLMClient:
    """Abstract base class for LLM backend clients."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=self._timeout,
                    read=self._timeout,
                    write=self._timeout,
                    pool=self._timeout,
                )
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def complete(
        self,
        *,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMGatewayResponse:
        """Complete a prompt. Must be implemented by subclasses."""
        raise NotImplementedError

    async def complete_batch(
        self,
        requests: list[dict[str, Any]],
    ) -> list[LLMGatewayResponse]:
        """Complete multiple prompts. Default implementation calls complete sequentially."""
        results = []
        for req in requests:
            response = await self.complete(
                prompt=req["prompt"],
                temperature=req.get("temperature", 0.0),
                max_tokens=req.get("max_tokens", 4096),
                json_schema=req.get("json_schema"),
            )
            results.append(response)
        return results

    async def health_check(self) -> bool:
        """Check if the backend is healthy. Must be implemented by subclasses."""
        raise NotImplementedError

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation: ~4 chars per token."""
        return max(len(text) // 4, 1)

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate estimated cost using TokenEconomicsService."""
        return estimate_cost(self._model, input_tokens, output_tokens)


# ── Ollama Client ───────────────────────────────────────────────────────


class OllamaClient(BaseLLMClient):
    """Client for Ollama API."""

    async def complete(
        self,
        *,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMGatewayResponse:
        client = await self._get_client()

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        # Add structured output format if schema provided
        if json_schema is not None:
            payload["format"] = json_schema

        try:
            resp = await client.post(f"{self._base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Ollama API error {exc.response.status_code}: {exc}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Ollama API request failed: {exc}") from exc

        content = data.get("message", {}).get("content", "")
        usage = data.get("usage", {})  # Some versions may have usage
        input_tokens = usage.get("prompt_tokens", self._estimate_tokens(prompt))
        output_tokens = usage.get("completion_tokens", self._estimate_tokens(content))

        # Fallback to Ollama's native token counts if available
        if "prompt_eval_count" in data:
            input_tokens = data["prompt_eval_count"]
        if "eval_count" in data:
            output_tokens = data["eval_count"]

        cost_usd = self._calculate_cost(input_tokens, output_tokens)

        return LLMGatewayResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=data.get("model", self._model),
            cost_usd=cost_usd,
            backend="ollama",
        )

    async def health_check(self) -> bool:
        """Check Ollama health via /api/tags endpoint."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", [])
            return any(self._model in m.get("name", "") for m in models)
        except (httpx.RequestError, httpx.HTTPStatusError):
            return False


# ── VLLM Client ─────────────────────────────────────────────────────────


class VLLMClient(BaseLLMClient):
    """Client for vLLM (OpenAI-compatible API)."""

    async def complete(
        self,
        *,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMGatewayResponse:
        client = await self._get_client()

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Add structured output via response_format (OpenAI-compatible)
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "schema": json_schema,
                    "strict": True,
                },
            }

        try:
            resp = await client.post(f"{self._base_url}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"vLLM API error {exc.response.status_code}: {exc}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"vLLM API request failed: {exc}") from exc

        choice = data["choices"][0]
        content = choice["message"]["content"]
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", self._estimate_tokens(prompt))
        output_tokens = usage.get("completion_tokens", self._estimate_tokens(content))

        cost_usd = self._calculate_cost(input_tokens, output_tokens)

        return LLMGatewayResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=data.get("model", self._model),
            cost_usd=cost_usd,
            backend="vllm",
        )

    async def health_check(self) -> bool:
        """Check vLLM health via /v1/models endpoint."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._base_url}/v1/models")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", [])
            return any(self._model in m.get("id", "") for m in models)
        except (httpx.RequestError, httpx.HTTPStatusError):
            return False


# ── llama.cpp Client ────────────────────────────────────────────────────


class LlamaCppClient(BaseLLMClient):
    """Client for llama.cpp server (OpenAI-compatible API)."""

    async def complete(
        self,
        *,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMGatewayResponse:
        client = await self._get_client()

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Add structured output via response_format (OpenAI-compatible)
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "schema": json_schema,
                    "strict": True,
                },
            }

        try:
            resp = await client.post(f"{self._base_url}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"llama.cpp API error {exc.response.status_code}: {exc}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"llama.cpp API request failed: {exc}") from exc

        choice = data["choices"][0]
        content = choice["message"]["content"]
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", self._estimate_tokens(prompt))
        output_tokens = usage.get("completion_tokens", self._estimate_tokens(content))

        cost_usd = self._calculate_cost(input_tokens, output_tokens)

        return LLMGatewayResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=data.get("model", self._model),
            cost_usd=cost_usd,
            backend="llamacpp",
        )

    async def health_check(self) -> bool:
        """Check llama.cpp health via /v1/models endpoint."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._base_url}/v1/models")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", [])
            return any(self._model in m.get("id", "") for m in models)
        except (httpx.RequestError, httpx.HTTPStatusError):
            return False


# ── LocalLLMGateway ─────────────────────────────────────────────────────


class LocalLLMGateway:
    """
    Unified gateway for local LLM inference.

    Provides a single interface for multiple backends with:
    - Structured output via JSON schema
    - Batch completion for efficiency
    - Health checking
    - Token economics integration
    - Automatic retries
    """

    def __init__(
        self,
        *,
        config: GatewayConfig | None = None,
        token_economics: TokenEconomicsService | None = None,
        review_run_id: UUID | str | None = None,
        agent_name: str = "llm-gateway",
        repo_owner: str = "",
        repo_name: str = "",
    ) -> None:
        self._config = config or GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
        self._token_economics = token_economics
        self._review_run_id = review_run_id
        self._agent_name = agent_name
        self._repo_owner = repo_owner
        self._repo_name = repo_name

        # Create appropriate backend client
        self._client = self._create_client()

    def _create_client(self) -> BaseLLMClient:
        """Create the appropriate backend client based on config."""
        if self._config.backend == "ollama":
            return OllamaClient(
                base_url=self._config.base_url,
                model=self._config.model,
                timeout=self._config.timeout,
            )
        if self._config.backend == "vllm":
            return VLLMClient(
                base_url=self._config.base_url,
                model=self._config.model,
                timeout=self._config.timeout,
            )
        if self._config.backend == "llamacpp":
            return LlamaCppClient(
                base_url=self._config.base_url,
                model=self._config.model,
                timeout=self._config.timeout,
            )
        raise ValueError(f"Unknown backend: {self._config.backend}")

    def set_token_economics(
        self,
        token_economics: TokenEconomicsService,
        *,
        review_run_id: UUID | str,
        agent_name: str,
        repo_owner: str = "",
        repo_name: str = "",
    ) -> None:
        """Configure token economics metering."""
        self._token_economics = token_economics
        self._review_run_id = review_run_id
        self._agent_name = agent_name
        self._repo_owner = repo_owner
        self._repo_name = repo_name

    @property
    def config(self) -> GatewayConfig:
        return self._config

    @property
    def client(self) -> BaseLLMClient:
        return self._client

    async def complete(
        self,
        *,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMGatewayResponse:
        """
        Complete a single prompt with optional structured output.

        Args:
            prompt: The input prompt
            temperature: Sampling temperature (default from config)
            max_tokens: Maximum tokens in response (default from config)
            json_schema: Optional JSON schema for structured output

        Returns:
            LLMGatewayResponse with content and metadata
        """
        temperature = temperature if temperature is not None else self._config.temperature
        max_tokens = max_tokens if max_tokens is not None else self._config.max_tokens

        last_error = None
        for attempt in range(self._config.max_retries + 1):
            try:
                response = await self._client.complete(
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_schema=json_schema,
                )

                # Meter through TokenEconomicsService if configured
                if self._token_economics and self._review_run_id:
                    try:
                        await self._token_economics.record_call(
                            review_run_id=self._review_run_id
                            if isinstance(self._review_run_id, UUID)
                            else UUID(self._review_run_id),
                            agent_name=self._agent_name,
                            model=response.model,
                            input_tokens=response.input_tokens,
                            output_tokens=response.output_tokens,
                            repo_owner=self._repo_owner,
                            repo_name=self._repo_name,
                            org=self._repo_owner,
                        )
                    except (RuntimeError, OSError, ValueError) as exc:
                        logger.warning("Failed to meter LLM call: %s", exc)

                return response

            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt < self._config.max_retries:
                    logger.warning(
                        "LLM call failed (attempt %d/%d), retrying: %s",
                        attempt + 1,
                        self._config.max_retries + 1,
                        exc,
                    )
                else:
                    logger.error(
                        "LLM call failed after %d attempts: %s",
                        self._config.max_retries + 1,
                        exc,
                    )

        raise RuntimeError(
            f"Failed after {self._config.max_retries + 1} attempts. Last error: {last_error}"
        ) from last_error

    async def complete_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """
        Complete a prompt and parse response as structured JSON matching schema.

        Args:
            prompt: The input prompt
            schema: JSON schema for validation
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            max_retries: Override default max_retries for structured output

        Returns:
            Parsed dict matching the schema

        Raises:
            ValueError: If all retries fail to produce valid structured output
        """
        retries = max_retries if max_retries is not None else self._config.max_retries
        last_error = ""

        for attempt in range(retries + 1):
            try:
                response = await self.complete(
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_schema=schema,
                )

                # Try to parse JSON from the response
                json_str = _extract_json_from_text(response.content)
                parsed = json.loads(json_str)

                # Validate against schema
                errors = _validate_json_against_schema(parsed, schema)
                if not errors:
                    return parsed

                last_error = f"Schema validation errors: {'; '.join(errors)}"
                logger.warning(
                    "Structured LLM response schema mismatch (attempt %d/%d): %s",
                    attempt + 1,
                    retries + 1,
                    last_error,
                )

            except json.JSONDecodeError as exc:
                last_error = f"JSON parse error: {exc}"
                logger.warning(
                    "Structured LLM response not valid JSON (attempt %d/%d): %s",
                    attempt + 1,
                    retries + 1,
                    last_error,
                )
            except RuntimeError as exc:
                # Network/HTTP errors - retry
                last_error = f"Request error: {exc}"
                logger.warning(
                    "Structured LLM request failed (attempt %d/%d): %s",
                    attempt + 1,
                    retries + 1,
                    last_error,
                )

        raise ValueError(
            f"Failed to get valid structured response after {retries + 1} attempts. "
            f"Last error: {last_error}"
        )

    async def complete_batch(
        self,
        requests: list[dict[str, Any]],
    ) -> list[LLMGatewayResponse]:
        """
        Complete multiple prompts in batch.

        Args:
            requests: List of dicts with keys: prompt, temperature, max_tokens, json_schema

        Returns:
            List of LLMGatewayResponse objects
        """
        return await self._client.complete_batch(requests)

    async def health_check(self) -> bool:
        """Check if the backend is healthy."""
        return await self._client.health_check()

    async def close(self) -> None:
        """Close the gateway and underlying client."""
        await self._client.close()


# ── Configuration Loader ────────────────────────────────────────────────


def load_gateway_config_from_yaml(config_path: str) -> GatewayConfig:
    """
    Load GatewayConfig from .verdity.yml file.

    Expected format:
    ```yaml
    llm:
      default_model: "qwen2.5-7b-instruct"
      default_backend: "ollama"
      ollama_url: "http://localhost:11434"
      llama_cpp_path: null
      vllm_url: "http://localhost:8000"
    ```
    """
    with open(config_path) as f:
        data = yaml.safe_load(f)

    llm_config = data.get("llm", {})

    backend = llm_config.get("default_backend", "ollama")
    model = llm_config.get("default_model", "qwen2.5-7b-instruct")

    # Get base_url based on backend
    if backend == "ollama":
        base_url = llm_config.get("ollama_url", "http://localhost:11434")
    elif backend == "vllm":
        base_url = llm_config.get("vllm_url", "http://localhost:8000")
    elif backend == "llamacpp":
        base_url = llm_config.get("llama_cpp_url", "http://localhost:8080")
    else:
        base_url = ""

    return GatewayConfig(
        model=model,
        backend=backend,
        base_url=base_url,
    )


# ── Public exports ──────────────────────────────────────────────────────

__all__ = [
    "GatewayConfig",
    "LLMGatewayResponse",
    "LlamaCppClient",
    "LocalLLMGateway",
    "OllamaClient",
    "VLLMClient",
    "load_gateway_config_from_yaml",
]
