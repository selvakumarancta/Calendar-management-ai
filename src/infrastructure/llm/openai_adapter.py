"""
OpenAI LLM Adapter — implements LLMPort for OpenAI API.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.domain.interfaces.llm import LLMPort
from src.domain.value_objects import TokenUsage


class OpenAIAdapter(LLMPort):
    """Concrete LLM adapter for OpenAI API."""

    BASE_URL = "https://api.openai.com/v1"

    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-4o-mini",
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
                "Authorization": f"Bearer {api_key}",
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
        payload: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        response = await self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()

        # Track token usage
        usage = data.get("usage", {})
        self._last_usage = TokenUsage(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            model=payload["model"],
        )

        return data

    async def generate_embedding(self, text: str) -> list[float]:
        response = await self._client.post(
            "/embeddings",
            json={
                "model": "text-embedding-3-small",
                "input": text,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]

    def get_last_token_usage(self) -> TokenUsage | None:
        return self._last_usage

    async def close(self) -> None:
        await self._client.aclose()
