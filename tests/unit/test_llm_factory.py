"""
Tests for LLM Factory — verifies multi-provider adapter creation.
"""

from __future__ import annotations

import pytest

from src.infrastructure.llm.anthropic_adapter import AnthropicAdapter
from src.infrastructure.llm.factory import LLMProvider, create_llm_adapter
from src.infrastructure.llm.openai_adapter import OpenAIAdapter


class TestLLMFactory:
    """Test LLM provider factory."""

    @pytest.mark.unit
    def test_create_anthropic_adapter(self) -> None:
        adapter = create_llm_adapter(
            provider="anthropic",
            api_key="test-key",
            default_model="claude-haiku-3-20250414",
        )
        assert isinstance(adapter, AnthropicAdapter)

    @pytest.mark.unit
    def test_create_openai_adapter(self) -> None:
        adapter = create_llm_adapter(
            provider="openai",
            api_key="test-key",
            default_model="gpt-4o-mini",
        )
        assert isinstance(adapter, OpenAIAdapter)

    @pytest.mark.unit
    def test_case_insensitive_provider(self) -> None:
        adapter = create_llm_adapter(provider="Anthropic", api_key="test-key")
        assert isinstance(adapter, AnthropicAdapter)

    @pytest.mark.unit
    def test_unsupported_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            create_llm_adapter(provider="gemini", api_key="test-key")

    @pytest.mark.unit
    def test_default_model_anthropic(self) -> None:
        adapter = create_llm_adapter(provider="anthropic", api_key="test-key")
        assert adapter._default_model == "claude-haiku-3-20250414"  # type: ignore[attr-defined]

    @pytest.mark.unit
    def test_default_model_openai(self) -> None:
        adapter = create_llm_adapter(provider="openai", api_key="test-key")
        assert adapter._default_model == "gpt-4o-mini"  # type: ignore[attr-defined]

    @pytest.mark.unit
    def test_llm_provider_enum_values(self) -> None:
        assert LLMProvider.ANTHROPIC == "anthropic"
        assert LLMProvider.OPENAI == "openai"
