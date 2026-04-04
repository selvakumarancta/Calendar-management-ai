"""Port: LLM — abstract interface for language model interactions."""

from __future__ import annotations

import abc

from src.domain.value_objects import TokenUsage


class LLMPort(abc.ABC):
    """Abstract LLM interface — model-agnostic."""

    @abc.abstractmethod
    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ) -> dict:
        """Send a chat completion request. Returns raw response dict."""
        ...

    @abc.abstractmethod
    async def generate_embedding(self, text: str) -> list[float]:
        """Generate an embedding vector for semantic cache."""
        ...

    @abc.abstractmethod
    def get_last_token_usage(self) -> TokenUsage | None:
        """Return token usage from the last call."""
        ...
