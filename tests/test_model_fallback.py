"""Tests for the multi-model fallback module."""

from __future__ import annotations

import asyncio
import time

import pytest

from verdity.model_fallback import (
    FallbackState,
    ModelConfig,
    MultiModelFallback,
)


class TestModelConfig:
    def test_model_config_defaults(self):
        config = ModelConfig(name="test", provider="openai", model_id="gpt-4o")
        assert config.max_tokens == 4096
        assert config.temperature == 0.1
        assert config.timeout_seconds == 30.0
        assert config.priority == 0


class TestFallbackState:
    def test_initial_state(self):
        state = FallbackState(model_name="test")
        assert state.failure_count == 0
        assert state.is_available is True
        assert state.cooldown_until == 0.0


class TestMultiModelFallback:
    def test_default_models(self):
        fallback = MultiModelFallback()
        assert len(fallback._models) == 3
        assert fallback._models[0].name == "deepseek-primary"

    def test_get_available_models_all_available(self):
        fallback = MultiModelFallback()
        available = fallback.get_available_models()
        assert len(available) == 3

    def test_record_failure_applies_cooldown(self):
        fallback = MultiModelFallback()
        fallback.record_failure("deepseek-primary")
        state = fallback._state["deepseek-primary"]
        assert state.failure_count == 1
        assert state.cooldown_until > time.monotonic()

    def test_record_success_resets_failures(self):
        fallback = MultiModelFallback()
        fallback.record_failure("deepseek-primary")
        fallback.record_failure("deepseek-primary")
        fallback.record_success("deepseek-primary")
        state = fallback._state["deepseek-primary"]
        assert state.failure_count == 0
        assert state.is_available is True

    def test_model_unavailable_after_max_failures(self):
        fallback = MultiModelFallback()
        for _ in range(MultiModelFallback.MAX_FAILURES_BEFORE_DISABLE):
            fallback.record_failure("deepseek-primary")
        available = fallback.get_available_models()
        assert all(m.name != "deepseek-primary" for m in available)

    def test_cooldown_prevents_immediate_retry(self):
        fallback = MultiModelFallback()
        fallback.record_failure("deepseek-primary")
        available = fallback.get_available_models()
        assert all(m.name != "deepseek-primary" for m in available)


class TestMultiModelFallbackCall:
    @pytest.mark.asyncio
    async def test_successful_call(self):
        fallback = MultiModelFallback(
            models=[ModelConfig(name="m1", provider="test", model_id="test-model", priority=0)]
        )

        async def mock_call(model_id: str, prompt: str, **kwargs):
            return "success"

        result = await fallback.call("test prompt", mock_call)
        assert result.success is True
        assert result.content == "success"
        assert result.model_used == "test-model"

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self):
        call_count = 0

        async def failing_then_succeeding(model_id: str, prompt: str, **kwargs):
            nonlocal call_count
            call_count += 1
            if model_id == "fail-model":
                raise ValueError("Model failed")
            return "success from fallback"

        fallback = MultiModelFallback(
            models=[
                ModelConfig(name="m1", provider="test", model_id="fail-model", priority=0),
                ModelConfig(name="m2", provider="test", model_id="good-model", priority=1),
            ]
        )

        result = await fallback.call("test prompt", failing_then_succeeding)
        assert result.success is True
        assert result.content == "success from fallback"
        assert result.model_used == "good-model"
        assert result.attempt == 2

    @pytest.mark.asyncio
    async def test_timeout_triggers_fallback(self):
        async def timeout_call(model_id: str, prompt: str, **kwargs):
            if model_id == "slow-model":
                await asyncio.sleep(100)  # Will timeout
            return "fast response"

        fallback = MultiModelFallback(
            models=[
                ModelConfig(
                    name="m1",
                    provider="test",
                    model_id="slow-model",
                    priority=0,
                    timeout_seconds=0.1,
                ),
                ModelConfig(
                    name="m2",
                    provider="test",
                    model_id="fast-model",
                    priority=1,
                    timeout_seconds=1.0,
                ),
            ]
        )

        result = await fallback.call("test prompt", timeout_call)
        assert result.success is True
        assert result.model_used == "fast-model"

    @pytest.mark.asyncio
    async def test_all_models_fail(self):
        async def always_fails(model_id: str, prompt: str, **kwargs):
            raise RuntimeError("All models down")

        fallback = MultiModelFallback(
            models=[ModelConfig(name="m1", provider="test", model_id="m1", priority=0)]
        )

        result = await fallback.call("test prompt", always_fails, max_retries=2)
        assert result.success is False
        assert "All attempts failed" in result.error

    @pytest.mark.asyncio
    async def test_no_available_models(self):
        fallback = MultiModelFallback(
            models=[ModelConfig(name="m1", provider="test", model_id="m1", priority=0)]
        )
        # Disable the model
        for _ in range(MultiModelFallback.MAX_FAILURES_BEFORE_DISABLE):
            fallback.record_failure("m1")

        async def mock_call(model_id: str, prompt: str, **kwargs):
            return "should not reach"

        result = await fallback.call("test prompt", mock_call)
        assert result.success is False
        assert "All models are in cooldown" in result.error


class TestMultiModelFallbackStatus:
    def test_get_status(self):
        fallback = MultiModelFallback()
        status = fallback.get_status()
        assert "models" in status
        assert "available_count" in status
        assert "total_count" in status
        assert status["total_count"] == 3
        assert status["available_count"] == 3

    def test_get_status_with_failures(self):
        fallback = MultiModelFallback()
        fallback.record_failure("deepseek-primary")
        status = fallback.get_status()
        primary = next(m for m in status["models"] if m["name"] == "deepseek-primary")
        assert primary["failure_count"] == 1
        assert primary["cooldown_remaining"] > 0

    def test_record_failure_unknown_model_ignored(self):
        fallback = MultiModelFallback()
        # Should not raise, just return
        fallback.record_failure("nonexistent-model")

    def test_record_success_unknown_model_ignored(self):
        fallback = MultiModelFallback()
        # Should not raise, just return
        fallback.record_success("nonexistent-model")

    def test_model_disabled_after_max_failures(self):
        fallback = MultiModelFallback(
            models=[ModelConfig(name="m1", provider="test", model_id="m1", priority=0)]
        )
        # Disable by exceeding max failures
        for _ in range(MultiModelFallback.MAX_FAILURES_BEFORE_DISABLE + 1):
            fallback.record_failure("m1")
        # Set cooldown to past so get_available_models reaches the failure_count check
        fallback._state["m1"].cooldown_until = 0.0
        available = fallback.get_available_models()
        assert len(available) == 0
