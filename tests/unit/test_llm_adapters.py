"""
Unit tests for AnthropicAdapter and OpenAIAdapter.
All HTTP calls are intercepted with pytest-mock / unittest.mock.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.value_objects import TokenUsage
from src.infrastructure.llm.anthropic_adapter import AnthropicAdapter
from src.infrastructure.llm.openai_adapter import OpenAIAdapter

# ---------------------------------------------------------------------------
# AnthropicAdapter
# ---------------------------------------------------------------------------


class TestAnthropicAdapterInit:
    @pytest.mark.unit
    def test_default_attrs(self):
        a = AnthropicAdapter(api_key="test-key")
        assert a._api_key == "test-key"
        assert a._temperature == 0.1
        assert a._last_usage is None

    @pytest.mark.unit
    def test_custom_model_and_temp(self):
        a = AnthropicAdapter(
            api_key="k", default_model="claude-3-opus", temperature=0.5
        )
        assert a._default_model == "claude-3-opus"
        assert a._temperature == 0.5

    @pytest.mark.unit
    def test_get_last_usage_returns_none_initially(self):
        a = AnthropicAdapter(api_key="k")
        assert a.get_last_token_usage() is None


class TestAnthropicConvertTools:
    @pytest.mark.unit
    def test_converts_openai_function_tool(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        result = AnthropicAdapter._convert_tools(tools)
        assert result[0]["name"] == "get_weather"
        assert result[0]["description"] == "Get weather"
        assert "input_schema" in result[0]

    @pytest.mark.unit
    def test_passes_through_non_function_tool(self):
        tools = [{"type": "retrieval", "name": "search"}]
        result = AnthropicAdapter._convert_tools(tools)
        assert result[0]["type"] == "retrieval"

    @pytest.mark.unit
    def test_empty_tools_list(self):
        assert AnthropicAdapter._convert_tools([]) == []


class TestAnthropicGenerateEmbedding:
    @pytest.mark.unit
    async def test_returns_float_list(self):
        a = AnthropicAdapter(api_key="k")
        embedding = await a.generate_embedding("hello world")
        assert isinstance(embedding, list)
        assert all(isinstance(v, float) for v in embedding)

    @pytest.mark.unit
    async def test_deterministic_for_same_text(self):
        a = AnthropicAdapter(api_key="k")
        e1 = await a.generate_embedding("test text")
        e2 = await a.generate_embedding("test text")
        assert e1 == e2

    @pytest.mark.unit
    async def test_different_texts_produce_different_embeddings(self):
        a = AnthropicAdapter(api_key="k")
        e1 = await a.generate_embedding("hello")
        e2 = await a.generate_embedding("world")
        assert e1 != e2


class TestAnthropicChatCompletion:
    @pytest.mark.unit
    async def test_separates_system_message(self):
        """System messages are extracted from the messages list."""
        a = AnthropicAdapter(api_key="k")
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "content": [{"type": "text", "text": "OK"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        fake_resp.raise_for_status = MagicMock()

        with patch.object(
            a._client, "post", new=AsyncMock(return_value=fake_resp)
        ) as mock_post:
            result = await a.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hi"},
                ]
            )
        call_payload = mock_post.call_args[1]["json"]
        assert "system" in call_payload
        assert call_payload["system"] == "You are a helpful assistant."
        # user message should not include the system role
        roles = [m["role"] for m in call_payload["messages"]]
        assert "system" not in roles

    @pytest.mark.unit
    async def test_records_token_usage(self):
        a = AnthropicAdapter(api_key="k")
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "content": [],
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }
        fake_resp.raise_for_status = MagicMock()

        with patch.object(a._client, "post", new=AsyncMock(return_value=fake_resp)):
            await a.chat_completion(messages=[{"role": "user", "content": "Hello"}])

        usage = a.get_last_token_usage()
        assert isinstance(usage, TokenUsage)
        assert usage.prompt_tokens == 20
        assert usage.completion_tokens == 10

    @pytest.mark.unit
    async def test_passes_tools_when_provided(self):
        a = AnthropicAdapter(api_key="k")
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "content": [],
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
        fake_resp.raise_for_status = MagicMock()

        openai_tools = [
            {
                "type": "function",
                "function": {"name": "f", "description": "d", "parameters": {}},
            }
        ]
        with patch.object(
            a._client, "post", new=AsyncMock(return_value=fake_resp)
        ) as mock_post:
            await a.chat_completion(
                messages=[{"role": "user", "content": "use tool"}],
                tools=openai_tools,
            )

        payload = mock_post.call_args[1]["json"]
        assert "tools" in payload
        assert payload["tools"][0]["name"] == "f"


# ---------------------------------------------------------------------------
# OpenAIAdapter
# ---------------------------------------------------------------------------


class TestOpenAIAdapterInit:
    @pytest.mark.unit
    def test_default_attrs(self):
        o = OpenAIAdapter(api_key="sk-test")
        assert o._api_key == "sk-test"
        assert o._default_model == "gpt-4o-mini"
        assert o._last_usage is None

    @pytest.mark.unit
    def test_get_last_usage_returns_none_initially(self):
        o = OpenAIAdapter(api_key="sk-test")
        assert o.get_last_token_usage() is None

    @pytest.mark.unit
    def test_custom_model(self):
        o = OpenAIAdapter(api_key="k", default_model="gpt-4o")
        assert o._default_model == "gpt-4o"


class TestOpenAIChatCompletion:
    @pytest.mark.unit
    async def test_records_token_usage(self):
        o = OpenAIAdapter(api_key="k")
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "choices": [{"message": {"content": "Hi"}}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 8},
        }
        fake_resp.raise_for_status = MagicMock()

        with patch.object(o._client, "post", new=AsyncMock(return_value=fake_resp)):
            await o.chat_completion(messages=[{"role": "user", "content": "Hello"}])

        usage = o.get_last_token_usage()
        assert isinstance(usage, TokenUsage)
        assert usage.prompt_tokens == 15
        assert usage.completion_tokens == 8

    @pytest.mark.unit
    async def test_sends_tools_with_tool_choice(self):
        o = OpenAIAdapter(api_key="k")
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "choices": [],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        }
        fake_resp.raise_for_status = MagicMock()

        tools = [
            {"type": "function", "function": {"name": "get_time", "parameters": {}}}
        ]
        with patch.object(
            o._client, "post", new=AsyncMock(return_value=fake_resp)
        ) as mock_post:
            await o.chat_completion(
                messages=[{"role": "user", "content": "What time?"}], tools=tools
            )

        payload = mock_post.call_args[1]["json"]
        assert "tools" in payload
        assert payload["tool_choice"] == "auto"

    @pytest.mark.unit
    async def test_generate_embedding_returns_floats(self):
        o = OpenAIAdapter(api_key="k")
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
        fake_resp.raise_for_status = MagicMock()

        with patch.object(o._client, "post", new=AsyncMock(return_value=fake_resp)):
            embedding = await o.generate_embedding("test text")

        assert embedding == [0.1, 0.2, 0.3]
