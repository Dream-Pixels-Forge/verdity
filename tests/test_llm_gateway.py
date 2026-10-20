"""
Tests for LLM Gateway module (Issue #50).

Gate test: LocalLLMGateway with mocked backends completes structured output
matching provided JSON schema for all three backends (ollama, llama.cpp, vLLM).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Import will fail initially - this is the RED phase
from verdity.llm_gateway import (
    GatewayConfig,
    LocalLLMGateway,
    OllamaClient,
    LlamaCppClient,
    VLLMClient,
    LLMGatewayResponse,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _mock_ollama_response(content: str, model: str = "qwen2.5-7b-instruct") -> dict:
    """Build a mock Ollama chat completion response."""
    return {
        "message": {"content": content},
        "model": model,
        "done": True,
        "prompt_eval_count": 100,
        "eval_count": len(content) // 4,
    }


def _mock_ollama_generate_response(content: str, model: str = "qwen2.5-7b-instruct") -> dict:
    """Build a mock Ollama generate response (non-chat)."""
    return {
        "response": content,
        "model": model,
        "done": True,
        "prompt_eval_count": 100,
        "eval_count": len(content) // 4,
    }


def _mock_vllm_response(content: str, model: str = "qwen2.5-7b-instruct") -> dict:
    """Build a mock vLLM/OpenAI-compatible chat completion response."""
    return {
        "choices": [{"message": {"content": content}}],
        "model": model,
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": len(content) // 4,
        },
    }


def _mock_llamacpp_response(content: str, model: str = "qwen2.5-7b-instruct") -> dict:
    """Build a mock llama.cpp server response (OpenAI-compatible)."""
    return {
        "choices": [{"message": {"content": content}}],
        "model": model,
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": len(content) // 4,
        },
    }


# ── Gate Test ───────────────────────────────────────────────────────────


class TestGateIssue50:
    """Issue #50 gate: LocalLLMGateway works with all three backends."""

    @pytest.mark.asyncio
    async def test_gate_ollama_backend_structured_output(self):
        """Ollama backend produces structured output matching schema."""
        schema = {
            "type": "object",
            "required": ["summary", "severity"],
            "properties": {
                "summary": {"type": "string"},
                "severity": {"type": "string"},
            },
        }

        finding_json = json.dumps(
            {"summary": "Test finding", "severity": "high"}
        )
        mock_response = _mock_ollama_response(finding_json)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            # Client is created directly, not via async with
            MockClient.return_value = mock_instance

            config = GatewayConfig(
                model="qwen2.5-7b-instruct",
                backend="ollama",
                base_url="http://localhost:11434",
            )
            gateway = LocalLLMGateway(config=config)

            result = await gateway.complete_structured(
                prompt="Analyze this code",
                schema=schema,
            )

            assert isinstance(result, dict)
            assert result["summary"] == "Test finding"
            assert result["severity"] == "high"

    @pytest.mark.asyncio
    async def test_gate_vllm_backend_structured_output(self):
        """vLLM backend produces structured output matching schema."""
        schema = {
            "type": "object",
            "required": ["summary", "severity"],
            "properties": {
                "summary": {"type": "string"},
                "severity": {"type": "string"},
            },
        }

        finding_json = json.dumps(
            {"summary": "vLLM finding", "severity": "critical"}
        )
        mock_response = _mock_vllm_response(finding_json)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            config = GatewayConfig(
                model="qwen2.5-7b-instruct",
                backend="vllm",
                base_url="http://localhost:8000",
            )
            gateway = LocalLLMGateway(config=config)

            result = await gateway.complete_structured(
                prompt="Analyze this code",
                schema=schema,
            )

            assert isinstance(result, dict)
            assert result["summary"] == "vLLM finding"
            assert result["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_gate_llamacpp_backend_structured_output(self):
        """llama.cpp backend produces structured output matching schema."""
        schema = {
            "type": "object",
            "required": ["summary", "severity"],
            "properties": {
                "summary": {"type": "string"},
                "severity": {"type": "string"},
            },
        }

        finding_json = json.dumps(
            {"summary": "llama.cpp finding", "severity": "medium"}
        )
        mock_response = _mock_llamacpp_response(finding_json)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            config = GatewayConfig(
                model="qwen2.5-7b-instruct",
                backend="llamacpp",
                base_url="http://localhost:8080",
            )
            gateway = LocalLLMGateway(config=config)

            result = await gateway.complete_structured(
                prompt="Analyze this code",
                schema=schema,
            )

            assert isinstance(result, dict)
            assert result["summary"] == "llama.cpp finding"
            assert result["severity"] == "medium"


# ── GatewayConfig ───────────────────────────────────────────────────────


class TestGatewayConfig:
    """Tests for GatewayConfig dataclass."""

    def test_config_defaults(self):
        config = GatewayConfig(
            model="test-model",
            backend="ollama",
        )
        assert config.model == "test-model"
        assert config.backend == "ollama"
        assert config.base_url == "http://localhost:11434"  # default for ollama
        assert config.timeout == 30.0
        assert config.max_retries == 2
        assert config.temperature == 0.0
        assert config.max_tokens == 4096

    def test_config_vllm_default_url(self):
        config = GatewayConfig(
            model="test-model",
            backend="vllm",
        )
        assert config.base_url == "http://localhost:8000"

    def test_config_llamacpp_default_url(self):
        config = GatewayConfig(
            model="test-model",
            backend="llamacpp",
        )
        assert config.base_url == "http://localhost:8080"

    def test_config_custom_values(self):
        config = GatewayConfig(
            model="custom-model",
            backend="ollama",
            base_url="http://custom:11434",
            timeout=60.0,
            max_retries=3,
            temperature=0.5,
            max_tokens=2048,
        )
        assert config.model == "custom-model"
        assert config.base_url == "http://custom:11434"
        assert config.timeout == 60.0
        assert config.max_retries == 3
        assert config.temperature == 0.5
        assert config.max_tokens == 2048

    def test_config_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="Invalid backend"):
            GatewayConfig(model="test", backend="invalid")


# ── LLMGatewayResponse ──────────────────────────────────────────────────


class TestLLMGatewayResponse:
    """Tests for the LLMGatewayResponse dataclass."""

    def test_response_fields(self):
        resp = LLMGatewayResponse(
            content="test content",
            input_tokens=100,
            output_tokens=50,
            model="test-model",
            cost_usd=0.001,
            backend="ollama",
        )
        assert resp.content == "test content"
        assert resp.input_tokens == 100
        assert resp.output_tokens == 50
        assert resp.model == "test-model"
        assert resp.cost_usd == 0.001
        assert resp.backend == "ollama"

    def test_response_is_dataclass(self):
        from dataclasses import fields

        resp = LLMGatewayResponse(
            content="",
            input_tokens=0,
            output_tokens=0,
            model="",
            cost_usd=0.0,
            backend="",
        )
        field_names = {f.name for f in fields(resp)}
        assert field_names == {
            "content",
            "input_tokens",
            "output_tokens",
            "model",
            "cost_usd",
            "backend",
        }


# ── LocalLLMGateway ─────────────────────────────────────────────────────


class TestLocalLLMGateway:
    """Tests for the LocalLLMGateway class."""

    def test_init_ollama_creates_ollama_client(self):
        config = GatewayConfig(model="test", backend="ollama")
        gateway = LocalLLMGateway(config=config)
        assert isinstance(gateway._client, OllamaClient)

    def test_init_vllm_creates_vllm_client(self):
        config = GatewayConfig(model="test", backend="vllm")
        gateway = LocalLLMGateway(config=config)
        assert isinstance(gateway._client, VLLMClient)

    def test_init_llamacpp_creates_llamacpp_client(self):
        config = GatewayConfig(model="test", backend="llamacpp")
        gateway = LocalLLMGateway(config=config)
        assert isinstance(gateway._client, LlamaCppClient)

    @pytest.mark.asyncio
    async def test_complete_basic(self):
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        mock_response = _mock_ollama_response("Hello world")

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content="Hello world",
                input_tokens=50,
                output_tokens=25,
                model="test-model",
                cost_usd=0.0001,
                backend="ollama",
            )

            response = await gateway.complete(prompt="Say hello")

            assert isinstance(response, LLMGatewayResponse)
            assert response.content == "Hello world"
            assert response.model == "test-model"
            assert response.backend == "ollama"
            mock_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_with_parameters(self):
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content="Response",
                input_tokens=50,
                output_tokens=25,
                model="test-model",
                cost_usd=0.0001,
                backend="ollama",
            )

            await gateway.complete(
                prompt="Test prompt",
                temperature=0.7,
                max_tokens=100,
            )

            # Verify parameters passed through
            call_kwargs = mock_complete.call_args.kwargs
            assert call_kwargs["temperature"] == 0.7
            assert call_kwargs["max_tokens"] == 100

    @pytest.mark.asyncio
    async def test_complete_structured_success(self):
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        finding_json = json.dumps(
            {"summary": "Structured finding", "severity": "high", "file": "test.py"}
        )
        schema = {
            "type": "object",
            "required": ["summary", "severity"],
            "properties": {
                "summary": {"type": "string"},
                "severity": {"type": "string"},
                "file": {"type": "string"},
            },
        }

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content=finding_json,
                input_tokens=100,
                output_tokens=50,
                model="test-model",
                cost_usd=0.0005,
                backend="ollama",
            )

            result = await gateway.complete_structured(
                prompt="Analyze",
                schema=schema,
            )

            assert result["summary"] == "Structured finding"
            assert result["severity"] == "high"
            assert result["file"] == "test.py"

    @pytest.mark.asyncio
    async def test_complete_structured_with_json_in_markdown(self):
        """Handle JSON wrapped in markdown code fences."""
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        # JSON wrapped in markdown
        markdown_json = '```json\n{"summary": "Found issue", "severity": "critical"}\n```'
        schema = {
            "type": "object",
            "required": ["summary", "severity"],
            "properties": {
                "summary": {"type": "string"},
                "severity": {"type": "string"},
            },
        }

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content=markdown_json,
                input_tokens=100,
                output_tokens=50,
                model="test-model",
                cost_usd=0.0005,
                backend="ollama",
            )

            result = await gateway.complete_structured(
                prompt="Analyze",
                schema=schema,
            )

            assert result["summary"] == "Found issue"
            assert result["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_complete_structured_retries_on_schema_mismatch(self):
        """Retry when response doesn't match schema."""
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=2)
        gateway = LocalLLMGateway(config=config)

        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }

        call_count = 0

        async def mock_complete(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: wrong type for name
                return LLMGatewayResponse(
                    content='{"name": 123}',
                    input_tokens=50,
                    output_tokens=25,
                    model="test-model",
                    cost_usd=0.0001,
                    backend="ollama",
                )
            # Second call: correct
            return LLMGatewayResponse(
                content='{"name": "correct"}',
                input_tokens=50,
                output_tokens=25,
                model="test-model",
                cost_usd=0.0001,
                backend="ollama",
            )

        with patch.object(gateway._client, "complete", new=mock_complete):
            result = await gateway.complete_structured(
                prompt="Test",
                schema=schema,
            )

            assert result == {"name": "correct"}
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_complete_structured_raises_after_max_retries(self):
        """Raise ValueError after all retries exhausted."""
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=1)
        gateway = LocalLLMGateway(config=config)

        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content='{"name": 123}',  # Wrong type
                input_tokens=50,
                output_tokens=25,
                model="test-model",
                cost_usd=0.0001,
                backend="ollama",
            )

            with pytest.raises(ValueError, match="Failed to get valid structured response"):
                await gateway.complete_structured(
                    prompt="Test",
                    schema=schema,
                )

    @pytest.mark.asyncio
    async def test_complete_batch(self):
        """Batch completion for efficiency."""
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        requests = [
            {"prompt": "Prompt 1", "temperature": 0.0, "max_tokens": 100},
            {"prompt": "Prompt 2", "temperature": 0.5, "max_tokens": 200},
            {"prompt": "Prompt 3", "temperature": 0.0, "max_tokens": 100},
        ]

        expected_responses = [
            LLMGatewayResponse(
                content=f"Response {i}",
                input_tokens=50,
                output_tokens=25,
                model="test-model",
                cost_usd=0.0001,
                backend="ollama",
            )
            for i in range(3)
        ]

        with patch.object(gateway._client, "complete_batch", new_callable=AsyncMock) as mock_batch:
            mock_batch.return_value = expected_responses

            results = await gateway.complete_batch(requests)

            assert len(results) == 3
            assert all(isinstance(r, LLMGatewayResponse) for r in results)
            assert results[0].content == "Response 0"
            assert results[1].content == "Response 1"
            assert results[2].content == "Response 2"
            mock_batch.assert_called_once_with(requests)

    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        """Health check returns True for healthy backend."""
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        with patch.object(gateway._client, "health_check", new_callable=AsyncMock) as mock_health:
            mock_health.return_value = True

            result = await gateway.health_check()

            assert result is True
            mock_health.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self):
        """Health check returns False for unhealthy backend."""
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        with patch.object(gateway._client, "health_check", new_callable=AsyncMock) as mock_health:
            mock_health.return_value = False

            result = await gateway.health_check()

            assert result is False


# ── Backend Clients Interface ───────────────────────────────────────────


class TestBackendClientsInterface:
    """Verify all backends implement the same interface."""

    def test_all_clients_have_required_methods(self):
        """All backend clients must implement complete, complete_batch, health_check."""
        for client_class in [OllamaClient, VLLMClient, LlamaCppClient]:
            assert hasattr(client_class, "complete")
            assert hasattr(client_class, "complete_batch")
            assert hasattr(client_class, "health_check")
            # Verify they're async
            import inspect
            assert inspect.iscoroutinefunction(client_class.complete)
            assert inspect.iscoroutinefunction(client_class.complete_batch)
            assert inspect.iscoroutinefunction(client_class.health_check)


# ── OllamaClient ────────────────────────────────────────────────────────


class TestOllamaClient:
    """Tests for OllamaClient."""

    @pytest.mark.asyncio
    async def test_complete_chat_endpoint(self):
        client = OllamaClient(
            base_url="http://localhost:11434",
            model="qwen2.5-7b-instruct",
            timeout=30.0,
        )

        mock_response = _mock_ollama_response("Test response")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            response = await client.complete(
                prompt="Test prompt",
                temperature=0.0,
                max_tokens=100,
            )

            assert isinstance(response, LLMGatewayResponse)
            assert response.content == "Test response"
            assert response.backend == "ollama"
            assert response.model == "qwen2.5-7b-instruct"

            # Verify Ollama API call format
            call_args = mock_instance.post.call_args
            assert "/api/chat" in call_args[0][0]
            payload = call_args[1]["json"]
            assert payload["model"] == "qwen2.5-7b-instruct"
            assert payload["messages"][0]["role"] == "user"
            assert payload["messages"][0]["content"] == "Test prompt"
            assert payload["options"]["temperature"] == 0.0
            assert payload["options"]["num_predict"] == 100

    @pytest.mark.asyncio
    async def test_complete_structured_output_native(self):
        """Ollama supports native structured output via format parameter."""
        client = OllamaClient(
            base_url="http://localhost:11434",
            model="qwen2.5-7b-instruct",
        )

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        mock_response = _mock_ollama_response('{"name": "test"}')

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            response = await client.complete(
                prompt="Test",
                temperature=0.0,
                max_tokens=100,
                json_schema=schema,
            )

            # Verify format parameter was sent for structured output
            call_args = mock_instance.post.call_args
            payload = call_args[1]["json"]
            assert "format" in payload
            assert payload["format"] == schema

    @pytest.mark.asyncio
    async def test_complete_batch(self):
        client = OllamaClient(
            base_url="http://localhost:11434",
            model="qwen2.5-7b-instruct",
        )

        requests = [
            {"prompt": "Prompt 1", "temperature": 0.0, "max_tokens": 100},
            {"prompt": "Prompt 2", "temperature": 0.0, "max_tokens": 100},
        ]

        mock_responses = [
            _mock_ollama_response("Response 1"),
            _mock_ollama_response("Response 2"),
        ]

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_responses[call_count]
            mock_resp.raise_for_status = MagicMock()
            call_count += 1
            return mock_resp

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = mock_post
            MockClient.return_value = mock_instance

            results = await client.complete_batch(requests)

            assert len(results) == 2
            assert all(r.backend == "ollama" for r in results)
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_health_check(self):
        client = OllamaClient(
            base_url="http://localhost:11434",
            model="qwen2.5-7b-instruct",
        )

        # Healthy response
        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"models": [{"name": "qwen2.5-7b-instruct"}]}
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is True

        # Unhealthy response
        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.RequestError("Connection refused"))
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is False


# ── VLLMClient ──────────────────────────────────────────────────────────


class TestVLLMClient:
    """Tests for VLLMClient (OpenAI-compatible)."""

    @pytest.mark.asyncio
    async def test_complete_openai_compatible(self):
        client = VLLMClient(
            base_url="http://localhost:8000",
            model="qwen2.5-7b-instruct",
            timeout=30.0,
        )

        mock_response = _mock_vllm_response("Test response")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            response = await client.complete(
                prompt="Test prompt",
                temperature=0.0,
                max_tokens=100,
            )

            assert isinstance(response, LLMGatewayResponse)
            assert response.content == "Test response"
            assert response.backend == "vllm"
            assert response.model == "qwen2.5-7b-instruct"

            # Verify OpenAI-compatible API call format
            call_args = mock_instance.post.call_args
            assert "/v1/chat/completions" in call_args[0][0]
            payload = call_args[1]["json"]
            assert payload["model"] == "qwen2.5-7b-instruct"
            assert payload["messages"][0]["role"] == "user"
            assert payload["messages"][0]["content"] == "Test prompt"
            assert payload["temperature"] == 0.0
            assert payload["max_tokens"] == 100

    @pytest.mark.asyncio
    async def test_complete_with_json_schema(self):
        """vLLM supports structured output via response_format."""
        client = VLLMClient(
            base_url="http://localhost:8000",
            model="qwen2.5-7b-instruct",
        )

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        mock_response = _mock_vllm_response('{"name": "test"}')

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            response = await client.complete(
                prompt="Test",
                temperature=0.0,
                max_tokens=100,
                json_schema=schema,
            )

            # Verify response_format was sent (vLLM wraps schema in OpenAI format)
            call_args = mock_instance.post.call_args
            payload = call_args[1]["json"]
            assert "response_format" in payload
            assert payload["response_format"]["type"] == "json_schema"
            json_schema = payload["response_format"]["json_schema"]
            assert json_schema["name"] == "structured_output"
            assert json_schema["schema"] == schema
            assert json_schema["strict"] is True

    @pytest.mark.asyncio
    async def test_complete_batch(self):
        client = VLLMClient(
            base_url="http://localhost:8000",
            model="qwen2.5-7b-instruct",
        )

        requests = [
            {"prompt": "Prompt 1", "temperature": 0.0, "max_tokens": 100},
            {"prompt": "Prompt 2", "temperature": 0.0, "max_tokens": 100},
        ]

        mock_responses = [
            _mock_vllm_response("Response 1"),
            _mock_vllm_response("Response 2"),
        ]

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_responses[call_count]
            mock_resp.raise_for_status = MagicMock()
            call_count += 1
            return mock_resp

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = mock_post
            MockClient.return_value = mock_instance

            results = await client.complete_batch(requests)

            assert len(results) == 2
            assert all(r.backend == "vllm" for r in results)

    @pytest.mark.asyncio
    async def test_health_check(self):
        client = VLLMClient(
            base_url="http://localhost:8000",
            model="qwen2.5-7b-instruct",
        )

        # Healthy - models endpoint returns list
        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": [{"id": "qwen2.5-7b-instruct"}]}
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is True


# ── LlamaCppClient ──────────────────────────────────────────────────────


class TestLlamaCppClient:
    """Tests for LlamaCppClient (OpenAI-compatible)."""

    @pytest.mark.asyncio
    async def test_complete_openai_compatible(self):
        client = LlamaCppClient(
            base_url="http://localhost:8080",
            model="qwen2.5-7b-instruct",
            timeout=30.0,
        )

        mock_response = _mock_llamacpp_response("Test response")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            response = await client.complete(
                prompt="Test prompt",
                temperature=0.0,
                max_tokens=100,
            )

            assert isinstance(response, LLMGatewayResponse)
            assert response.content == "Test response"
            assert response.backend == "llamacpp"
            assert response.model == "qwen2.5-7b-instruct"

            # Verify OpenAI-compatible API call format
            call_args = mock_instance.post.call_args
            assert "/v1/chat/completions" in call_args[0][0]
            payload = call_args[1]["json"]
            assert payload["model"] == "qwen2.5-7b-instruct"

    @pytest.mark.asyncio
    async def test_complete_batch(self):
        client = LlamaCppClient(
            base_url="http://localhost:8080",
            model="qwen2.5-7b-instruct",
        )

        requests = [
            {"prompt": "Prompt 1", "temperature": 0.0, "max_tokens": 100},
            {"prompt": "Prompt 2", "temperature": 0.0, "max_tokens": 100},
        ]

        mock_responses = [
            _mock_llamacpp_response("Response 1"),
            _mock_llamacpp_response("Response 2"),
        ]

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_responses[call_count]
            mock_resp.raise_for_status = MagicMock()
            call_count += 1
            return mock_resp

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = mock_post
            MockClient.return_value = mock_instance

            results = await client.complete_batch(requests)

            assert len(results) == 2
            assert all(r.backend == "llamacpp" for r in results)

    @pytest.mark.asyncio
    async def test_health_check(self):
        client = LlamaCppClient(
            base_url="http://localhost:8080",
            model="qwen2.5-7b-instruct",
        )

        # Healthy
        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": [{"id": "qwen2.5-7b-instruct"}]}
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is True


# ── Token Economics Integration ─────────────────────────────────────────


class TestTokenEconomicsIntegration:
    """Tests for TokenEconomicsService integration."""

    @pytest.mark.asyncio
    async def test_complete_meters_through_token_economics(self):
        """Gateway meters calls through TokenEconomicsService when configured."""
        from verdity.token_economics import TokenEconomicsService

        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        # Create mock TokenEconomicsService
        mock_te = MagicMock(spec=TokenEconomicsService)
        mock_te.record_call = AsyncMock(return_value=0.001)
        gateway.set_token_economics(mock_te, review_run_id=uuid.uuid4(), agent_name="test-agent")

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content="Test response",
                input_tokens=100,
                output_tokens=50,
                model="test-model",
                cost_usd=0.001,
                backend="ollama",
            )

            await gateway.complete(prompt="Test")

            # Verify token economics was called
            mock_te.record_call.assert_called_once()
            call_kwargs = mock_te.record_call.call_args.kwargs
            assert call_kwargs["agent_name"] == "test-agent"
            assert call_kwargs["model"] == "test-model"
            assert call_kwargs["input_tokens"] == 100
            assert call_kwargs["output_tokens"] == 50

    @pytest.mark.asyncio
    async def test_complete_without_token_economics_works(self):
        """Gateway works without TokenEconomicsService configured."""
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)
        # No token economics set

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content="Test response",
                input_tokens=100,
                output_tokens=50,
                model="test-model",
                cost_usd=0.001,
                backend="ollama",
            )

            response = await gateway.complete(prompt="Test")

            assert response.content == "Test response"

    @pytest.mark.asyncio
    async def test_token_economics_failure_does_not_break_completion(self):
        """If token economics fails, completion still succeeds."""
        from verdity.token_economics import TokenEconomicsService

        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        # Create failing TokenEconomicsService
        class FailingTE:
            async def record_call(self, **kwargs):
                raise RuntimeError("Token economics DB down")

        gateway.set_token_economics(FailingTE(), review_run_id=uuid.uuid4(), agent_name="test-agent")

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content="Test response",
                input_tokens=100,
                output_tokens=50,
                model="test-model",
                cost_usd=0.001,
                backend="ollama",
            )

            # Should not raise
            response = await gateway.complete(prompt="Test")
            assert response.content == "Test response"


# ── Configuration from .verdity.yml ─────────────────────────────────────


class TestConfigFromYaml:
    """Tests for loading config from .verdity.yml."""

    def test_load_config_from_yaml(self, tmp_path):
        """GatewayConfig can be created from .verdity.yml llm section."""
        import yaml

        yaml_content = {
            "llm": {
                "default_model": "qwen2.5-7b-instruct",
                "default_backend": "ollama",
                "ollama_url": "http://localhost:11434",
                "llama_cpp_path": None,
                "vllm_url": "http://localhost:8000",
            }
        }

        config_file = tmp_path / ".verdity.yml"
        config_file.write_text(yaml.dump(yaml_content))

        # This should work once we implement the loader
        from verdity.llm_gateway import load_gateway_config_from_yaml

        config = load_gateway_config_from_yaml(config_file)
        assert config.model == "qwen2.5-7b-instruct"
        assert config.backend == "ollama"
        assert config.base_url == "http://localhost:11434"

    def test_env_var_override(self, tmp_path, monkeypatch):
        """Environment variables override .verdity.yml config."""
        import yaml

        yaml_content = {
            "llm": {
                "default_model": "qwen2.5-7b-instruct",
                "default_backend": "ollama",
                "ollama_url": "http://localhost:11434",
            }
        }

        config_file = tmp_path / ".verdity.yml"
        config_file.write_text(yaml.dump(yaml_content))

        monkeypatch.setenv("VERDITY_LLM_MODEL", "custom-model")
        monkeypatch.setenv("VERDITY_LLM_BACKEND", "vllm")
        monkeypatch.setenv("VERDITY_LLM_BASE_URL", "http://custom:8000")

        from verdity.llm_gateway import load_gateway_config_from_yaml

        config = load_gateway_config_from_yaml(config_file)
        assert config.model == "custom-model"
        assert config.backend == "vllm"
        assert config.base_url == "http://custom:8000"


# ── Error Handling ──────────────────────────────────────────────────────


class TestErrorHandling:
    """Tests for error handling and retries."""

    @pytest.mark.asyncio
    async def test_retry_on_http_error(self):
        """Retry on transient HTTP errors."""
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=2)
        gateway = LocalLLMGateway(config=config)

        call_count = 0

        async def mock_complete(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.RequestError("Transient network error")
            return LLMGatewayResponse(
                content="Success after retry",
                input_tokens=50,
                output_tokens=25,
                model="test-model",
                cost_usd=0.0001,
                backend="ollama",
            )

        with patch.object(gateway._client, "complete", new=mock_complete):
            response = await gateway.complete(prompt="Test")
            assert response.content == "Success after retry"
            assert call_count == 3

    @pytest.mark.asyncio
    async def test_max_retries_exceeded_raises(self):
        """Raise after max retries exceeded."""
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=2)
        gateway = LocalLLMGateway(config=config)

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.side_effect = httpx.RequestError("Persistent network error")

            with pytest.raises(RuntimeError, match="Failed after"):
                await gateway.complete(prompt="Test")


# ── Import Test (acceptance criterion) ──────────────────────────────────


class TestAcceptanceCriteria:
    """Verify acceptance criteria from Issue #50."""

    def test_import_works(self):
        """`python -c "from verdity.llm_gateway import LocalLLMGateway; g=LocalLLMGateway(); print('OK')"` works."""
        from verdity.llm_gateway import LocalLLMGateway, GatewayConfig

        config = GatewayConfig(model="test", backend="ollama")
        gateway = LocalLLMGateway(config=config)
        assert gateway is not None
        print("OK")


# Run with: pytest tests/test_llm_gateway.py -v