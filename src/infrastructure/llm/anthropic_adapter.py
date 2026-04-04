"""
Anthropic LLM Adapter — implements LLMPort for Claude API.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.domain.interfaces.llm import LLMPort
from src.domain.value_objects import TokenUsage


class AnthropicAdapter(LLMPort):
    """Concrete LLM adapter for Anthropic/Claude API."""

    BASE_URL = "https://api.anthropic.com/v1"

    def __init__(
        self,
        api_key: str,
        default_model: str = "claude-haiku-3-20250414",
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._last_usage: TokenUsage | None = None
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ) -> dict:
        # Separate system message from conversation messages
        system_text = ""
        conversation_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_text = msg.get("content", "")
            else:
                conversation_messages.append(msg)

        payload: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": conversation_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_text:
            payload["system"] = system_text

        if tools:
            # Convert OpenAI-style tools to Anthropic format
            payload["tools"] = self._convert_tools(tools)

        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        data = response.json()

        # Track token usage
        usage = data.get("usage", {})
        self._last_usage = TokenUsage(
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            model=payload["model"],
        )

        return data

    async def generate_embedding(self, text: str) -> list[float]:
        """Anthropic doesn't have an embedding API — use Voyager or fallback."""
        # For semantic cache, we can use a lightweight local embedding
        # or delegate to a dedicated embedding service.
        # This is a placeholder that returns a simple hash-based vector.
        import hashlib

        hash_bytes = hashlib.sha256(text.encode()).digest()
        # Generate a deterministic 256-dim pseudo-embedding
        return [float(b) / 255.0 for b in hash_bytes * 8]

    def get_last_token_usage(self) -> TokenUsage | None:
        return self._last_usage

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _convert_tools(openai_tools: list[dict]) -> list[dict]:
        """Convert OpenAI-format tool definitions to Anthropic format."""
        anthropic_tools = []
        for tool in openai_tools:
            if tool.get("type") == "function":
                func = tool["function"]
                anthropic_tools.append(
                    {
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {}),
                    }
                )
            else:
                # Already in a compatible format or pass through
                anthropic_tools.append(tool)
        return anthropic_tools
