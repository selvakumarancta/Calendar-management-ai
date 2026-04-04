"""
LLM Provider Factory — creates the appropriate LLM adapter based on configuration.
Supports: anthropic, openai — extensible for future providers.
"""

from __future__ import annotations

from enum import Enum

from src.domain.interfaces.llm import LLMPort


class LLMProvider(str, Enum):
    """Supported LLM providers."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


# Provider → LangChain chat model class mapping (for agent graph)
LANGCHAIN_CHAT_MODELS: dict[str, str] = {
    "anthropic": "langchain_anthropic.ChatAnthropic",
    "openai": "langchain_openai.ChatOpenAI",
}

# Provider → model name mappings for cost-tier routing
MODEL_TIERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "fast": "claude-haiku-3-20250414",
        "primary": "claude-sonnet-4-20250514",
    },
    "openai": {
        "fast": "gpt-4o-mini",
        "primary": "gpt-4o",
    },
}


def create_llm_adapter(
    provider: str,
    api_key: str,
    default_model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> LLMPort:
    """
    Factory function — creates the correct LLM adapter based on provider string.
    Extensible: add a new elif branch for any future provider.
    """
    provider_lower = provider.lower()

    if provider_lower == LLMProvider.ANTHROPIC:
        from src.infrastructure.llm.anthropic_adapter import AnthropicAdapter

        return AnthropicAdapter(
            api_key=api_key,
            default_model=default_model or MODEL_TIERS["anthropic"]["fast"],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    elif provider_lower == LLMProvider.OPENAI:
        from src.infrastructure.llm.openai_adapter import OpenAIAdapter

        return OpenAIAdapter(
            api_key=api_key,
            default_model=default_model or MODEL_TIERS["openai"]["fast"],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    else:
        supported = ", ".join(p.value for p in LLMProvider)
        raise ValueError(
            f"Unsupported LLM provider: '{provider}'. Supported: {supported}"
        )


def create_langchain_chat_model(
    provider: str,
    api_key: str,
    model: str,
    temperature: float = 0.1,
) -> object:
    """
    Factory function — creates the correct LangChain chat model for the agent graph.
    Returns a BaseChatModel instance (typed as object to avoid import issues).
    """
    provider_lower = provider.lower()

    if provider_lower == LLMProvider.ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=4096,
        )

    elif provider_lower == LLMProvider.OPENAI:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            temperature=temperature,
        )

    else:
        supported = ", ".join(p.value for p in LLMProvider)
        raise ValueError(
            f"Unsupported LLM provider: '{provider}'. Supported: {supported}"
        )
