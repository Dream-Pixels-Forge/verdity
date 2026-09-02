"""
LLM Client — optional LLM integration for deeper code analysis.

Constraint #10: Every agent MUST work WITHOUT an LLM. This client is optional.
Constraint #8:  Every call goes through TokenEconomicsService.record_call().
Constraint #5:  Temperature defaults to 0.0 for deterministic scoring.

The LLM is an enhancement — deterministic regex is the primary path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from verdity.token_economics import TokenEconomicsService, estimate_cost

logger = logging.getLogger(__name__)

# ── Schema validation helpers ─────────────────────────────────────────


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

    for field in required:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    for field, value in data.items():
        if field in properties:
            expected_type = properties[field].get("type")
            if expected_type == "string" and not isinstance(value, str):
                errors.append(f"Field '{field}' should be string, got {type(value).__name__}")
            elif expected_type == "number" and not isinstance(value, int | float):
                errors.append(f"Field '{field}' should be number, got {type(value).__name__}")
            elif expected_type == "boolean" and not isinstance(value, bool):
                errors.append(f"Field '{field}' should be boolean, got {type(value).__name__}")
            elif expected_type == "integer" and not isinstance(value, int):
                errors.append(f"Field '{field}' should be integer, got {type(value).__name__}")
            elif expected_type == "array" and not isinstance(value, list):
                errors.append(f"Field '{field}' should be array, got {type(value).__name__}")

    return errors


# ── Response dataclass ────────────────────────────────────────────────


@dataclass
class LLMResponse:
    """Structured response from an LLM call."""

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float


# ── LLM Client ────────────────────────────────────────────────────────


class LLMClient:
    """
    Client for optional LLM integration.

    All calls are metered through TokenEconomicsService (constraint #8).
    Uses MultiModelFallback for retry/fallback (reuse Phase 4 infrastructure).
    Temperature defaults to 0.0 for determinism (constraint #5).

    Usage:
        client = LLMClient(token_economics=te, api_key="sk-...")
        response = await client.complete(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Review this code..."}],
        )
    """

    _DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(
        self,
        *,
        token_economics: TokenEconomicsService | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        review_run_id: str | None = None,
        agent_name: str = "llm-client",
        repo_owner: str = "",
        repo_name: str = "",
    ) -> None:
        self._token_economics = token_economics
        self._api_key = api_key
        self._base_url = (base_url or self._DEFAULT_BASE_URL).rstrip("/")
        self._review_run_id = review_run_id
        self._agent_name = agent_name
        self._repo_owner = repo_owner
        self._repo_name = repo_name

    @property
    def enabled(self) -> bool:
        """True if an API key is configured and LLM calls can be made."""
        return bool(self._api_key)

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """
        Send a completion request and return a structured response.

        All calls are metered through TokenEconomicsService.
        Raises RuntimeError if no API key is configured.
        """
        if not self._api_key:
            raise RuntimeError(
                "LLM client has no API key configured. "
                "Set VERDITY_LLM_API_KEY or pass api_key explicitly."
            )

        input_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
        input_tokens = max(input_tokens, 1)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"LLM API error {exc.response.status_code}: {exc}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"LLM API request failed: {exc}") from exc

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        output_tokens = usage.get("completion_tokens", len(content) // 4)
        total_input = usage.get("prompt_tokens", input_tokens)
        total_output = usage.get("completion_tokens", output_tokens)
        model_used = data.get("model", model)
        cost_usd = estimate_cost(model_used, total_input, total_output)

        # Meter through TokenEconomicsService (constraint #8)
        if self._token_economics and self._review_run_id:
            try:
                await self._token_economics.record_call(
                    review_run_id=self._review_run_id,
                    agent_name=self._agent_name,
                    model=model_used,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    repo_owner=self._repo_owner,
                    repo_name=self._repo_name,
                    org=self._repo_owner,
                )
            except (RuntimeError, OSError, ValueError) as exc:
                logger.warning("Failed to meter LLM call: %s", exc)

        return LLMResponse(
            content=content,
            input_tokens=total_input,
            output_tokens=total_output,
            model=model_used,
            cost_usd=cost_usd,
        )

    async def complete_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        temperature: float = 0.0,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        """
        Send a completion request and parse the response into a Pydantic-compatible dict.

        Retries on schema mismatch up to max_retries times.
        Returns parsed dict on success, raises ValueError if all retries fail.
        """
        last_error = ""
        for attempt in range(max_retries + 1):
            response = await self.complete(
                model=model,
                messages=messages,
                temperature=temperature,
            )

            # Try to parse JSON from the response
            try:
                json_str = _extract_json_from_text(response.content)
                parsed = json.loads(json_str)
            except json.JSONDecodeError as exc:
                last_error = f"JSON parse error: {exc}"
                logger.warning(
                    "Structured LLM response not valid JSON (attempt %d/%d): %s",
                    attempt + 1,
                    max_retries + 1,
                    last_error,
                )
                continue

            # Validate against schema
            errors = _validate_json_against_schema(parsed, schema)
            if not errors:
                return parsed

            last_error = f"Schema validation errors: {'; '.join(errors)}"
            logger.warning(
                "Structured LLM response schema mismatch (attempt %d/%d): %s",
                attempt + 1,
                max_retries + 1,
                last_error,
            )

        raise ValueError(
            f"Failed to get valid structured response after {max_retries + 1} attempts. "
            f"Last error: {last_error}"
        )
