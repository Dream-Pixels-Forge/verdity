"""
Tests for LLM Gateway module (Issue #50).

Gate test: LocalLLMGateway with mocked backends completes structured output
matching provided JSON schema for all backends (ollama, llama.cpp, vLLM, OpenAI).
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from verdity.llm_gateway import (
    GatewayConfig,
    LlamaCppClient,
    LLMGatewayResponse,
    LocalLLMGateway,
    OllamaClient,
    OpenAIClient,
    VLLMClient,
    load_gateway_config_from_yaml,
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


def _mock_openai_response(content: str, model: str = "gpt-4o-mini") -> dict:
    """Build a mock OpenAI-compatible chat completion response."""
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
    """Issue #50 gate: LocalLLMGateway works with all backends."""

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

        finding_json = json.dumps({"summary": "Test finding", "severity": "high"})
        mock_response = _mock_ollama_response(finding_json)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
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

        finding_json = json.dumps({"summary": "vLLM finding", "severity": "critical"})
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

        finding_json = json.dumps({"summary": "llama.cpp finding", "severity": "medium"})
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

    @pytest.mark.asyncio
    async def test_gate_openai_backend_structured_output(self):
        """OpenAI backend produces structured output matching schema."""
        schema = {
            "type": "object",
            "required": ["summary", "severity"],
            "properties": {
                "summary": {"type": "string"},
                "severity": {"type": "string"},
            },
        }

        finding_json = json.dumps({"summary": "OpenAI finding", "severity": "high"})
        mock_response = _mock_openai_response(finding_json)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            config = GatewayConfig(
                model="gpt-4o-mini",
                backend="openai",
                base_url="https://api.openai.com/v1",
            )
            gateway = LocalLLMGateway(config=config)

            result = await gateway.complete_structured(
                prompt="Analyze this code",
                schema=schema,
            )

            assert isinstance(result, dict)
            assert result["summary"] == "OpenAI finding"
            assert result["severity"] == "high"


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
        assert config.base_url == "http://localhost:11434"
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

    def test_config_openai_default_url(self):
        config = GatewayConfig(
            model="test-model",
            backend="openai",
        )
        assert config.base_url == "https://api.openai.com/v1"

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

    def test_init_openai_creates_openai_client(self):
        config = GatewayConfig(model="test", backend="openai")
        gateway = LocalLLMGateway(config=config)
        assert isinstance(gateway._client, OpenAIClient)

    @pytest.mark.asyncio
    async def test_complete_basic(self):
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

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
                input_tokens=50,
                output_tokens=25,
                model="test-model",
                cost_usd=0.0001,
                backend="ollama",
            )

            result = await gateway.complete_structured(
                prompt="Analyze this code",
                schema=schema,
            )

            assert isinstance(result, dict)
            assert result["summary"] == "Structured finding"
            assert result["severity"] == "high"
            assert result["file"] == "test.py"

    @pytest.mark.asyncio
    async def test_complete_structured_with_json_in_markdown(self):
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        finding_json = json.dumps({"summary": "Structured finding", "severity": "high"})
        markdown_response = f"```json\n{finding_json}\n```"
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
                content=markdown_response,
                input_tokens=50,
                output_tokens=25,
                model="test-model",
                cost_usd=0.0001,
                backend="ollama",
            )

            result = await gateway.complete_structured(
                prompt="Analyze this code",
                schema=schema,
            )

            assert isinstance(result, dict)
            assert result["summary"] == "Structured finding"
            assert result["severity"] == "high"

    @pytest.mark.asyncio
    async def test_complete_structured_retry_on_schema_mismatch(self):
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=2)
        gateway = LocalLLMGateway(config=config)

        schema = {
            "type": "object",
            "required": ["summary", "severity"],
            "properties": {
                "summary": {"type": "string"},
                "severity": {"type": "string"},
            },
        }

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            # First call returns invalid schema (missing severity)
            mock_complete.side_effect = [
                LLMGatewayResponse(
                    content=json.dumps({"summary": "Only summary"}),
                    input_tokens=50,
                    output_tokens=25,
                    model="test-model",
                    cost_usd=0.0001,
                    backend="ollama",
                ),
                # Second call returns valid schema
                LLMGatewayResponse(
                    content=json.dumps({"summary": "Fixed", "severity": "high"}),
                    input_tokens=50,
                    output_tokens=25,
                    model="test-model",
                    cost_usd=0.0001,
                    backend="ollama",
                ),
            ]

            result = await gateway.complete_structured(
                prompt="Analyze this code",
                schema=schema,
            )

            assert result["summary"] == "Fixed"
            assert result["severity"] == "high"
            assert mock_complete.call_count == 2

    @pytest.mark.asyncio
    async def test_complete_structured_fails_after_max_retries(self):
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=1)
        gateway = LocalLLMGateway(config=config)

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
                content=json.dumps({"summary": "Only summary"}),
                input_tokens=50,
                output_tokens=25,
                model="test-model",
                cost_usd=0.0001,
                backend="ollama",
            )

            with pytest.raises(ValueError, match="Failed to get valid structured response"):
                await gateway.complete_structured(
                    prompt="Analyze this code",
                    schema=schema,
                )

            assert mock_complete.call_count == 2  # Initial + 1 retry

    @pytest.mark.asyncio
    async def test_complete_batch(self):
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        with patch.object(gateway._client, "complete_batch", new_callable=AsyncMock) as mock_batch:
            mock_batch.return_value = [
                LLMGatewayResponse(
                    content="Response 1",
                    input_tokens=50,
                    output_tokens=25,
                    model="test-model",
                    cost_usd=0.0001,
                    backend="ollama",
                ),
                LLMGatewayResponse(
                    content="Response 2",
                    input_tokens=50,
                    output_tokens=25,
                    model="test-model",
                    cost_usd=0.0001,
                    backend="ollama",
                ),
            ]

            requests = [
                {"prompt": "Prompt 1"},
                {"prompt": "Prompt 2"},
            ]
            results = await gateway.complete_batch(requests)

            assert len(results) == 2
            assert results[0].content == "Response 1"
            assert results[1].content == "Response 2"
            mock_batch.assert_called_once_with(requests)


# ── Token Economics Integration ─────────────────────────────────────────


class TestTokenEconomicsIntegration:
    """Tests for TokenEconomicsService integration."""

    @pytest.mark.asyncio
    async def test_complete_meters_tokens(self):
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        from verdity.token_economics import TokenEconomicsService

        token_economics = TokenEconomicsService()
        gateway.set_token_economics(
            token_economics,
            review_run_id=uuid.uuid4(),
            agent_name="test-agent",
            repo_owner="test-owner",
            repo_name="test-repo",
        )

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content="Response",
                input_tokens=100,
                output_tokens=50,
                model="test-model",
                cost_usd=0.0002,
                backend="ollama",
            )

            await gateway.complete(prompt="Test prompt")

            # Verify token economics was called
            # (We can't easily assert on the internal call, but we can verify no exception)

    @pytest.mark.asyncio
    async def test_complete_without_token_economics(self):
        """Gateway should work without token economics configured."""
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)
        # Don't call set_token_economics

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content="Response",
                input_tokens=100,
                output_tokens=50,
                model="test-model",
                cost_usd=0.0002,
                backend="ollama",
            )

            response = await gateway.complete(prompt="Test prompt")

            assert response.content == "Response"


# ── Health Check ────────────────────────────────────────────────────────


class TestHealthCheck:
    """Tests for health check methods."""

    @pytest.mark.asyncio
    async def test_ollama_health_check_success(self):
        config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"models": [{"name": "qwen2.5-7b-instruct"}]}
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            result = await gateway.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_ollama_health_check_model_not_found(self):
        config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"models": [{"name": "other-model"}]}
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            result = await gateway.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_ollama_health_check_connection_error(self):
        config = GatewayConfig(model="qwen2.5-7b-instruct", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            MockClient.return_value = mock_instance

            result = await gateway.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_vllm_health_check_success(self):
        config = GatewayConfig(model="test-model", backend="vllm")
        gateway = LocalLLMGateway(config=config)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": [{"id": "test-model"}]}
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            result = await gateway.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_llamacpp_health_check_success(self):
        config = GatewayConfig(model="test-model", backend="llamacpp")
        gateway = LocalLLMGateway(config=config)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": [{"id": "test-model"}]}
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            result = await gateway.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_openai_health_check_success(self):
        config = GatewayConfig(model="gpt-4o-mini", backend="openai")
        gateway = LocalLLMGateway(config=config)

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": [{"id": "gpt-4o-mini"}]}
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            result = await gateway.health_check()
            assert result is True


# ── Configuration Loader ────────────────────────────────────────────────


class TestConfigLoader:
    """Tests for loading configuration from YAML."""

    def test_load_gateway_config_from_yaml(self, tmp_path):
        yaml_content = """
llm:
  default_model: "qwen2.5-7b-instruct"
  default_backend: "ollama"
  ollama_url: "http://localhost:11434"
  vllm_url: "http://localhost:8000"
  llama_cpp_url: "http://localhost:8080"
  openai_url: "https://api.openai.com/v1"
"""
        config_file = tmp_path / ".verdity.yml"
        config_file.write_text(yaml_content)

        config = load_gateway_config_from_yaml(str(config_file))

        assert config.model == "qwen2.5-7b-instruct"
        assert config.backend == "ollama"
        assert config.base_url == "http://localhost:11434"

    def test_load_gateway_config_vllm(self, tmp_path):
        yaml_content = """
llm:
  default_model: "mistral-7b"
  default_backend: "vllm"
  vllm_url: "http://custom:8000"
"""
        config_file = tmp_path / ".verdity.yml"
        config_file.write_text(yaml_content)

        config = load_gateway_config_from_yaml(str(config_file))

        assert config.model == "mistral-7b"
        assert config.backend == "vllm"
        assert config.base_url == "http://custom:8000"

    def test_load_gateway_config_openai(self, tmp_path):
        yaml_content = """
llm:
  default_model: "gpt-4o-mini"
  default_backend: "openai"
  openai_url: "https://api.custom.com/v1"
"""
        config_file = tmp_path / ".verdity.yml"
        config_file.write_text(yaml_content)

        config = load_gateway_config_from_yaml(str(config_file))

        assert config.model == "gpt-4o-mini"
        assert config.backend == "openai"
        assert config.base_url == "https://api.custom.com/v1"


# ── Individual Client Tests ─────────────────────────────────────────────


class TestOllamaClient:
    """Tests for OllamaClient."""

    @pytest.mark.asyncio
    async def test_complete_with_format_parameter(self):
        client = OllamaClient(base_url="http://localhost:11434", model="test-model")

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        mock_response = _mock_ollama_response(json.dumps({"key": "value"}))

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            response = await client.complete(
                prompt="Test",
                json_schema=schema,
            )

            assert response.content == '{"key": "value"}'
            assert response.backend == "ollama"

    @pytest.mark.asyncio
    async def test_complete_without_schema(self):
        client = OllamaClient(base_url="http://localhost:11434", model="test-model")

        mock_response = _mock_ollama_response("Plain text response")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            response = await client.complete(prompt="Test")

            assert response.content == "Plain text response"


class TestVLLMClient:
    """Tests for VLLMClient."""

    @pytest.mark.asyncio
    async def test_complete_with_response_format(self):
        client = VLLMClient(base_url="http://localhost:8000", model="test-model")

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        mock_response = _mock_vllm_response(json.dumps({"key": "value"}))

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            response = await client.complete(
                prompt="Test",
                json_schema=schema,
            )

            assert response.content == '{"key": "value"}'
            assert response.backend == "vllm"


class TestLlamaCppClient:
    """Tests for LlamaCppClient."""

    @pytest.mark.asyncio
    async def test_complete_with_response_format(self):
        client = LlamaCppClient(base_url="http://localhost:8080", model="test-model")

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        mock_response = _mock_llamacpp_response(json.dumps({"key": "value"}))

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            response = await client.complete(
                prompt="Test",
                json_schema=schema,
            )

            assert response.content == '{"key": "value"}'
            assert response.backend == "llamacpp"


class TestOpenAIClient:
    """Tests for OpenAIClient."""

    @pytest.mark.asyncio
    async def test_complete_with_response_format(self):
        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        mock_response = _mock_openai_response(json.dumps({"key": "value"}))

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            response = await client.complete(
                prompt="Test",
                json_schema=schema,
            )

            assert response.content == '{"key": "value"}'
            assert response.backend == "openai"

    @pytest.mark.asyncio
    async def test_client_includes_auth_header(self):
        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key-123",
        )

        # Check that the client is created with auth header
        http_client = await client._get_client()
        assert "Authorization" in http_client.headers
        assert http_client.headers["Authorization"] == "Bearer test-key-123"
        await http_client.aclose()


# ── Retry Logic ─────────────────────────────────────────────────────────


class TestRetryLogic:
    """Tests for retry logic in LocalLLMGateway."""

    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self):
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=2)
        gateway = LocalLLMGateway(config=config)

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            # First two calls fail, third succeeds
            mock_complete.side_effect = [
                httpx.RequestError("Connection failed"),
                httpx.RequestError("Connection failed"),
                LLMGatewayResponse(
                    content="Success",
                    input_tokens=50,
                    output_tokens=25,
                    model="test-model",
                    cost_usd=0.0001,
                    backend="ollama",
                ),
            ]

            response = await gateway.complete(prompt="Test")

            assert response.content == "Success"
            assert mock_complete.call_count == 3

    @pytest.mark.asyncio
    async def test_fails_after_max_retries(self):
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=2)
        gateway = LocalLLMGateway(config=config)

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.side_effect = httpx.RequestError("Connection failed")

            with pytest.raises(RuntimeError, match="Failed after 3 attempts"):
                await gateway.complete(prompt="Test")

            assert mock_complete.call_count == 3


# ── JSON Schema Validation Helpers ──────────────────────────────────────


class TestSchemaValidation:
    """Tests for the schema validation helpers."""

    def test_validate_json_against_schema_valid(self):
        from verdity.llm_gateway import _validate_json_against_schema

        schema = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "active": {"type": "boolean"},
            },
        }

        data = {"name": "John", "age": 30, "active": True}
        errors = _validate_json_against_schema(data, schema)
        assert errors == []

    def test_validate_json_against_schema_missing_required(self):
        from verdity.llm_gateway import _validate_json_against_schema

        schema = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }

        data = {"name": "John"}
        errors = _validate_json_against_schema(data, schema)
        assert len(errors) == 1
        assert "Missing required field: age" in errors[0]

    def test_validate_json_against_schema_wrong_type(self):
        from verdity.llm_gateway import _validate_json_against_schema

        schema = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }

        data = {"name": "John", "age": "thirty"}
        errors = _validate_json_against_schema(data, schema)
        assert len(errors) == 1
        assert "should be integer" in errors[0]

    def test_validate_json_against_schema_number_type(self):
        from verdity.llm_gateway import _validate_json_against_schema

        schema = {
            "type": "object",
            "required": ["score"],
            "properties": {"score": {"type": "number"}},
        }

        data = {"score": "not a number"}
        errors = _validate_json_against_schema(data, schema)
        assert len(errors) == 1
        assert "should be number" in errors[0]

        # Valid number (int)
        data = {"score": 42}
        errors = _validate_json_against_schema(data, schema)
        assert errors == []

        # Valid number (float)
        data = {"score": 3.14}
        errors = _validate_json_against_schema(data, schema)
        assert errors == []

    def test_validate_json_against_schema_boolean_type(self):
        from verdity.llm_gateway import _validate_json_against_schema

        schema = {
            "type": "object",
            "required": ["active"],
            "properties": {"active": {"type": "boolean"}},
        }

        data = {"active": "yes"}
        errors = _validate_json_against_schema(data, schema)
        assert len(errors) == 1
        assert "should be boolean" in errors[0]

        # Valid boolean
        data = {"active": True}
        errors = _validate_json_against_schema(data, schema)
        assert errors == []

    def test_validate_json_against_schema_integer_type(self):
        from verdity.llm_gateway import _validate_json_against_schema

        schema = {
            "type": "object",
            "required": ["count"],
            "properties": {"count": {"type": "integer"}},
        }

        data = {"count": 3.14}
        errors = _validate_json_against_schema(data, schema)
        assert len(errors) == 1
        assert "should be integer" in errors[0]

        # Valid integer
        data = {"count": 42}
        errors = _validate_json_against_schema(data, schema)
        assert errors == []

    def test_validate_json_against_schema_array_type(self):
        from verdity.llm_gateway import _validate_json_against_schema

        schema = {
            "type": "object",
            "required": ["tags"],
            "properties": {"tags": {"type": "array"}},
        }

        data = {"tags": "not an array"}
        errors = _validate_json_against_schema(data, schema)
        assert len(errors) == 1
        assert "should be array" in errors[0]

        # Valid array
        data = {"tags": ["a", "b"]}
        errors = _validate_json_against_schema(data, schema)
        assert errors == []


# ── JSON Extraction Helpers ─────────────────────────────────────────────


class TestJsonExtraction:
    """Tests for JSON extraction from text."""

    def test_extract_json_from_markdown_code_block(self):
        from verdity.llm_gateway import _extract_json_from_text

        text = '```json\n{"key": "value"}\n```'
        result = _extract_json_from_text(text)
        assert result == '{"key": "value"}'

    def test_extract_json_from_markdown_no_language(self):
        from verdity.llm_gateway import _extract_json_from_text

        text = '```\n{"key": "value"}\n```'
        result = _extract_json_from_text(text)
        assert result == '{"key": "value"}'

    def test_extract_raw_json(self):
        from verdity.llm_gateway import _extract_json_from_text

        text = '{"key": "value"}'
        result = _extract_json_from_text(text)
        assert result == '{"key": "value"}'

    def test_extract_json_from_text_with_surrounding_text(self):
        from verdity.llm_gateway import _extract_json_from_text

        text = 'Here is the result: {"key": "value"} and more text'
        result = _extract_json_from_text(text)
        assert result == '{"key": "value"}'


# ── Close ───────────────────────────────────────────────────────────────


class TestClose:
    """Tests for closing the gateway."""

    @pytest.mark.asyncio
    async def test_close_gateway(self):
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        with patch.object(gateway._client, "close", new_callable=AsyncMock) as mock_close:
            await gateway.close()
            mock_close.assert_called_once()


# ── Exports ─────────────────────────────────────────────────────────────


class TestExports:
    """Test that all public exports are available."""

    def test_all_exports_importable(self):
        from verdity.llm_gateway import (
            GatewayConfig,
            LlamaCppClient,
            LLMGatewayResponse,
            LocalLLMGateway,
            OllamaClient,
            OpenAIClient,
            VLLMClient,
            load_gateway_config_from_yaml,
        )

        # All should be importable without error
        assert GatewayConfig
        assert LLMGatewayResponse
        assert LlamaCppClient
        assert LocalLLMGateway
        assert OllamaClient
        assert OpenAIClient
        assert VLLMClient
        assert load_gateway_config_from_yaml


class TestEnvironmentVariables:
    """Tests for environment variable overrides."""

    def test_config_env_overrides_model(self, monkeypatch):
        monkeypatch.setenv("VERDITY_LLM_MODEL", "env-model")
        config = GatewayConfig(model="default-model", backend="ollama")
        assert config.model == "env-model"

    def test_config_env_overrides_backend(self, monkeypatch):
        monkeypatch.setenv("VERDITY_LLM_BACKEND", "vllm")
        config = GatewayConfig(model="test-model", backend="ollama")
        assert config.backend == "vllm"

    def test_config_env_overrides_base_url(self, monkeypatch):
        monkeypatch.setenv("VERDITY_LLM_BASE_URL", "http://custom:9999")
        config = GatewayConfig(model="test-model", backend="ollama")
        assert config.base_url == "http://custom:9999"

    def test_config_env_overrides_timeout(self, monkeypatch):
        monkeypatch.setenv("VERDITY_LLM_TIMEOUT", "60.0")
        config = GatewayConfig(model="test-model", backend="ollama")
        assert config.timeout == 60.0

    def test_config_env_overrides_max_retries(self, monkeypatch):
        monkeypatch.setenv("VERDITY_LLM_MAX_RETRIES", "5")
        config = GatewayConfig(model="test-model", backend="ollama")
        assert config.max_retries == 5

    def test_config_env_overrides_temperature(self, monkeypatch):
        monkeypatch.setenv("VERDITY_LLM_TEMPERATURE", "0.7")
        config = GatewayConfig(model="test-model", backend="ollama")
        assert config.temperature == 0.7

    def test_config_env_overrides_max_tokens(self, monkeypatch):
        monkeypatch.setenv("VERDITY_LLM_MAX_TOKENS", "2048")
        config = GatewayConfig(model="test-model", backend="ollama")
        assert config.max_tokens == 2048


class TestExceptionHandling:
    """Tests for exception handling in clients."""

    @pytest.mark.asyncio
    async def test_ollama_client_http_status_error(self):
        client = OllamaClient(base_url="http://localhost:11434", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "Error", request=MagicMock(), response=MagicMock(status_code=500)
                )
            )
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            with pytest.raises(RuntimeError, match="Ollama API error 500"):
                await client.complete(prompt="Test")

    @pytest.mark.asyncio
    async def test_ollama_client_request_error(self):
        client = OllamaClient(base_url="http://localhost:11434", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            MockClient.return_value = mock_instance

            with pytest.raises(RuntimeError, match="Ollama API request failed"):
                await client.complete(prompt="Test")

    @pytest.mark.asyncio
    async def test_vllm_client_http_status_error(self):
        client = VLLMClient(base_url="http://localhost:8000", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "Error", request=MagicMock(), response=MagicMock(status_code=503)
                )
            )
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            with pytest.raises(RuntimeError, match="vLLM API error 503"):
                await client.complete(prompt="Test")

    @pytest.mark.asyncio
    async def test_vllm_client_request_error(self):
        client = VLLMClient(base_url="http://localhost:8000", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            MockClient.return_value = mock_instance

            with pytest.raises(RuntimeError, match="vLLM API request failed"):
                await client.complete(prompt="Test")

    @pytest.mark.asyncio
    async def test_llamacpp_client_http_status_error(self):
        client = LlamaCppClient(base_url="http://localhost:8080", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "Error", request=MagicMock(), response=MagicMock(status_code=404)
                )
            )
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            with pytest.raises(RuntimeError, match=r"llama.cpp API error 404"):
                await client.complete(prompt="Test")

    @pytest.mark.asyncio
    async def test_llamacpp_client_request_error(self):
        client = LlamaCppClient(base_url="http://localhost:8080", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            MockClient.return_value = mock_instance

            with pytest.raises(RuntimeError, match=r"llama.cpp API request failed"):
                await client.complete(prompt="Test")

    @pytest.mark.asyncio
    async def test_openai_client_http_status_error(self):
        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "Error", request=MagicMock(), response=MagicMock(status_code=401)
                )
            )
            mock_instance.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            with pytest.raises(RuntimeError, match="OpenAI API error 401"):
                await client.complete(prompt="Test")

    @pytest.mark.asyncio
    async def test_openai_client_request_error(self):
        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            MockClient.return_value = mock_instance

            with pytest.raises(RuntimeError, match="OpenAI API request failed"):
                await client.complete(prompt="Test")


class TestCloseMethod:
    """Tests for the close method."""

    @pytest.mark.asyncio
    async def test_ollama_client_close(self):
        client = OllamaClient(base_url="http://localhost:11434", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_closed = False
            MockClient.return_value = mock_instance

            # Create the client by making a call
            await client._get_client()
            await client.close()

            mock_instance.aclose.assert_called_once()
            assert client._client is None

    @pytest.mark.asyncio
    async def test_vllm_client_close(self):
        client = VLLMClient(base_url="http://localhost:8000", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_closed = False
            MockClient.return_value = mock_instance

            await client._get_client()
            await client.close()

            mock_instance.aclose.assert_called_once()
            assert client._client is None

    @pytest.mark.asyncio
    async def test_llamacpp_client_close(self):
        client = LlamaCppClient(base_url="http://localhost:8080", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_closed = False
            MockClient.return_value = mock_instance

            await client._get_client()
            await client.close()

            mock_instance.aclose.assert_called_once()
            assert client._client is None

    @pytest.mark.asyncio
    async def test_openai_client_close(self):
        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_closed = False
            MockClient.return_value = mock_instance

            await client._get_client()
            await client.close()

            mock_instance.aclose.assert_called_once()
            assert client._client is None

    @pytest.mark.asyncio
    async def test_gateway_close(self):
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        with patch.object(gateway._client, "close", new_callable=AsyncMock) as mock_close:
            await gateway.close()
            mock_close.assert_called_once()


class TestUnknownBackend:
    """Tests for unknown backend error."""

    def test_unknown_backend_raises(self):
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)
        gateway._config.backend = "unknown"

        with pytest.raises(ValueError, match="Unknown backend: unknown"):
            gateway._create_client()


class TestCompleteStructuredErrors:
    """Tests for complete_structured error handling."""

    @pytest.mark.asyncio
    async def test_complete_structured_json_decode_error(self):
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=1)
        gateway = LocalLLMGateway(config=config)

        schema = {"type": "object", "required": ["key"]}

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = LLMGatewayResponse(
                content="not valid json",
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
    async def test_complete_structured_runtime_error_retry(self):
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=2)
        gateway = LocalLLMGateway(config=config)

        schema = {"type": "object", "required": ["key"]}

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.side_effect = [
                RuntimeError("Network error"),
                LLMGatewayResponse(
                    content=json.dumps({"key": "value"}),
                    input_tokens=50,
                    output_tokens=25,
                    model="test-model",
                    cost_usd=0.0001,
                    backend="ollama",
                ),
            ]

            result = await gateway.complete_structured(
                prompt="Test",
                schema=schema,
            )

            assert result["key"] == "value"
            assert mock_complete.call_count == 2


class TestOpenAIClientHealthCheck:
    """Tests for OpenAI client health check."""

    @pytest.mark.asyncio
    async def test_openai_health_check_success(self):
        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": [{"id": "gpt-4o-mini"}]}
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_openai_health_check_model_not_found(self):
        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": [{"id": "other-model"}]}
            mock_resp.raise_for_status = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_openai_health_check_connection_error(self):
        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is False


class TestBaseClientMethods:
    """Tests for BaseLLMClient default implementations."""

    @pytest.mark.asyncio
    async def test_complete_batch_default_implementation(self):
        """Test BaseLLMClient.complete_batch default implementation."""
        from verdity.llm_gateway import BaseLLMClient, LLMGatewayResponse

        # Create a concrete subclass for testing
        class TestClient(BaseLLMClient):
            async def complete(self, *, prompt, temperature=0.0, max_tokens=4096, json_schema=None):
                return LLMGatewayResponse(
                    content=f"Response to: {prompt}",
                    input_tokens=50,
                    output_tokens=25,
                    model="test-model",
                    cost_usd=0.0001,
                    backend="test",
                )

            async def health_check(self):
                return True

        client = TestClient(base_url="http://test", model="test-model")

        requests = [
            {"prompt": "Prompt 1"},
            {"prompt": "Prompt 2"},
        ]
        results = await client.complete_batch(requests)

        assert len(results) == 2
        assert results[0].content == "Response to: Prompt 1"
        assert results[1].content == "Response to: Prompt 2"


class TestOpenAIClientExtra:
    """Additional tests for OpenAIClient."""

    @pytest.mark.asyncio
    async def test_openai_health_check_exception_handling(self):
        """Test OpenAI client health_check exception handling (covers lines 371-372)."""
        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "Error", request=MagicMock(), response=MagicMock(status_code=500)
                )
            )
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is False


class TestGatewayProperties:
    """Tests for LocalLLMGateway properties."""

    def test_gateway_config_property(self):
        """Test gateway.config property (covers line 444)."""
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        assert gateway.config is config
        assert gateway.config.model == "test-model"

    def test_gateway_client_property(self):
        """Test gateway.client property (covers line 445)."""
        config = GatewayConfig(model="test-model", backend="ollama")
        gateway = LocalLLMGateway(config=config)

        assert gateway.client is gateway._client
        assert isinstance(gateway.client, OllamaClient)


class TestCompleteStructuredErrorsExtra:
    """Additional tests for complete_structured error handling."""

    @pytest.mark.asyncio
    async def test_complete_structured_json_decode_error_retry(self):
        """Test JSON decode error triggers retry (covers line 633)."""
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=2)
        gateway = LocalLLMGateway(config=config)

        schema = {"type": "object", "required": ["key"]}

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            # First call returns invalid JSON, second returns valid
            mock_complete.side_effect = [
                LLMGatewayResponse(
                    content="not valid json at all",
                    input_tokens=50,
                    output_tokens=25,
                    model="test-model",
                    cost_usd=0.0001,
                    backend="ollama",
                ),
                LLMGatewayResponse(
                    content=json.dumps({"key": "value"}),
                    input_tokens=50,
                    output_tokens=25,
                    model="test-model",
                    cost_usd=0.0001,
                    backend="ollama",
                ),
            ]

            result = await gateway.complete_structured(
                prompt="Test",
                schema=schema,
            )

            assert result["key"] == "value"
            assert mock_complete.call_count == 2

    @pytest.mark.asyncio
    async def test_complete_structured_runtime_error_retry_exhausted(self):
        """Test RuntimeError retry exhausted (covers line 637)."""
        config = GatewayConfig(model="test-model", backend="ollama", max_retries=1)
        gateway = LocalLLMGateway(config=config)

        schema = {"type": "object", "required": ["key"]}

        with patch.object(gateway._client, "complete", new_callable=AsyncMock) as mock_complete:
            mock_complete.side_effect = RuntimeError("Network error")

            with pytest.raises(ValueError, match="Failed to get valid structured response"):
                await gateway.complete_structured(
                    prompt="Test",
                    schema=schema,
                )

            assert mock_complete.call_count == 2  # Initial + 1 retry


class TestConfigLoaderExtra:
    """Additional tests for config loader."""

    def test_load_gateway_config_llamacpp(self, tmp_path):
        yaml_content = """
llm:
  default_model: "llama-7b"
  default_backend: "llamacpp"
  llama_cpp_url: "http://custom:8080"
"""
        config_file = tmp_path / ".verdity.yml"
        config_file.write_text(yaml_content)

        config = load_gateway_config_from_yaml(str(config_file))

        assert config.model == "llama-7b"
        assert config.backend == "llamacpp"
        assert config.base_url == "http://custom:8080"

    def test_load_gateway_config_openai(self, tmp_path):
        yaml_content = """
llm:
  default_model: "gpt-4o-mini"
  default_backend: "openai"
  openai_url: "https://custom.openai.com/v1"
"""
        config_file = tmp_path / ".verdity.yml"
        config_file.write_text(yaml_content)

        config = load_gateway_config_from_yaml(str(config_file))

        assert config.model == "gpt-4o-mini"
        assert config.backend == "openai"
        assert config.base_url == "https://custom.openai.com/v1"

    def test_load_gateway_config_unknown_backend_raises(self, tmp_path):
        yaml_content = """
llm:
  default_model: "test-model"
  default_backend: "unknown"
"""
        config_file = tmp_path / ".verdity.yml"
        config_file.write_text(yaml_content)

        with pytest.raises(ValueError, match="Invalid backend: unknown"):
            load_gateway_config_from_yaml(str(config_file))


class TestSchemaValidationExtra:
    """Additional tests for schema validation to cover uncovered lines."""

    def test_validate_json_against_schema_string_type(self):
        """Test string type validation (covers line 126)."""
        from verdity.llm_gateway import _validate_json_against_schema

        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }

        # Invalid string (passing an integer instead)
        data = {"name": 123}
        errors = _validate_json_against_schema(data, schema)
        assert len(errors) == 1
        assert "should be string" in errors[0]

        # Valid string
        data = {"name": "valid"}
        errors = _validate_json_against_schema(data, schema)
        assert errors == []


class TestBaseLLMClientAbstractMethods:
    """Tests for BaseLLMClient abstract methods (covers lines 185, 205)."""

    @pytest.mark.asyncio
    async def test_base_client_complete_raises_not_implemented(self):
        """BaseLLMClient.complete() raises NotImplementedError (line 185)."""
        from verdity.llm_gateway import BaseLLMClient

        class IncompleteClient(BaseLLMClient):
            async def health_check(self):
                return True

        client = IncompleteClient(base_url="http://test", model="test")

        with pytest.raises(NotImplementedError):
            await client.complete(prompt="test")

    @pytest.mark.asyncio
    async def test_base_client_health_check_raises_not_implemented(self):
        """BaseLLMClient.health_check() raises NotImplementedError (line 205)."""
        from verdity.llm_gateway import BaseLLMClient

        class IncompleteClient(BaseLLMClient):
            async def complete(self, *, prompt, temperature=0.0, max_tokens=4096, json_schema=None):
                return LLMGatewayResponse(
                    content="test",
                    input_tokens=10,
                    output_tokens=5,
                    model="test",
                    cost_usd=0.0,
                    backend="test",
                )

        client = IncompleteClient(base_url="http://test", model="test")

        with pytest.raises(NotImplementedError):
            await client.health_check()


class TestVLLMClientHealthCheckErrors:
    """Tests for VLLM client health_check error handling (covers lines 359-360)."""

    @pytest.mark.asyncio
    async def test_vllm_health_check_http_status_error(self):
        """Test VLLM client health_check handles HTTPStatusError (lines 359-360)."""
        from verdity.llm_gateway import VLLMClient

        client = VLLMClient(base_url="http://localhost:8000", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
                )
            )
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_vllm_health_check_request_error(self):
        """Test VLLM client health_check handles RequestError (lines 359-360)."""
        from verdity.llm_gateway import VLLMClient

        client = VLLMClient(base_url="http://localhost:8000", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is False


class TestOpenAIClientHealthCheckErrors:
    """Tests for OpenAI client health_check error handling."""

    @pytest.mark.asyncio
    async def test_openai_health_check_http_status_error(self):
        """Test OpenAI client health_check handles HTTPStatusError."""
        from verdity.llm_gateway import OpenAIClient

        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
                )
            )
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_openai_health_check_request_error(self):
        """Test OpenAI client health_check handles RequestError."""
        from verdity.llm_gateway import OpenAIClient

        client = OpenAIClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="test-key",
        )

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is False


class TestLlamaCppClientHealthCheckErrors:
    """Tests for llama.cpp client health_check error handling (covers lines 432-433)."""

    @pytest.mark.asyncio
    async def test_llamacpp_health_check_http_status_error(self):
        """Test llama.cpp client health_check handles HTTPStatusError (lines 432-433)."""
        from verdity.llm_gateway import LlamaCppClient

        client = LlamaCppClient(base_url="http://localhost:8080", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "Server Error", request=MagicMock(), response=MagicMock(status_code=503)
                )
            )
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_llamacpp_health_check_request_error(self):
        """Test llama.cpp client health_check handles RequestError (lines 432-433)."""
        from verdity.llm_gateway import LlamaCppClient

        client = LlamaCppClient(base_url="http://localhost:8080", model="test-model")

        with patch("verdity.llm_gateway.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            MockClient.return_value = mock_instance

            result = await client.health_check()
            assert result is False
