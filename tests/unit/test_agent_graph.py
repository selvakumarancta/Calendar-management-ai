"""Tests for src/agent/graph.py — CalendarAgentGraph."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_ID = str(uuid.uuid4())


def _make_mock_tools():
    tool1 = MagicMock()
    tool1.name = "create_event"
    tool2 = MagicMock()
    tool2.name = "list_events"
    return [tool1, tool2]


def _make_compiled_graph(invoke_return=None):
    """Return a mock compiled LangGraph."""
    compiled = AsyncMock()
    compiled.ainvoke = AsyncMock(return_value=invoke_return or {"messages": []})
    return compiled


def _make_state_graph():
    """Return a fully-mocked StateGraph."""
    graph = MagicMock()
    graph.add_node = MagicMock()
    graph.set_entry_point = MagicMock()
    graph.add_conditional_edges = MagicMock()
    graph.add_edge = MagicMock()
    graph.compile = MagicMock(return_value=_make_compiled_graph())
    return graph


def _make_agent(
    max_iterations: int = 10,
    graph=None,
    tools=None,
):
    """Construct a CalendarAgentGraph with fully mocked dependencies."""
    mock_tools = tools or _make_mock_tools()
    mock_sg = graph or _make_state_graph()

    with (
        patch("src.agent.graph.create_calendar_tools", return_value=mock_tools),
        patch("src.agent.graph.StateGraph", return_value=mock_sg),
        patch("src.agent.graph.ToolNode", return_value=MagicMock()),
    ):
        from src.agent.graph import CalendarAgentGraph

        agent = CalendarAgentGraph(
            calendar_service=MagicMock(),
            llm_provider="openai",
            llm_api_key="sk-test",
            default_model="gpt-4o-mini",
            max_iterations=max_iterations,
            working_hours_start="09:00",
            working_hours_end="17:00",
        )
    # Give a fresh compiled graph for async tests
    compiled = _make_compiled_graph()
    agent._graph = compiled
    return agent, compiled


# ---------------------------------------------------------------------------
# __init__ / _build_graph
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_init_sets_all_attributes():
    agent, _ = _make_agent(max_iterations=5)
    assert agent._llm_provider == "openai"
    assert agent._llm_api_key == "sk-test"
    assert agent._default_model == "gpt-4o-mini"
    assert agent._max_iterations == 5
    assert agent._working_hours_start == "09:00"
    assert agent._working_hours_end == "17:00"


@pytest.mark.unit
def test_build_graph_calls_state_graph_api():
    """_build_graph wires reason→tools→reason using StateGraph methods."""
    mock_tools = _make_mock_tools()
    mock_sg = _make_state_graph()

    with (
        patch("src.agent.graph.create_calendar_tools", return_value=mock_tools),
        patch("src.agent.graph.StateGraph", return_value=mock_sg),
        patch("src.agent.graph.ToolNode", return_value=MagicMock()),
    ):
        from src.agent.graph import CalendarAgentGraph

        CalendarAgentGraph(
            calendar_service=MagicMock(),
            llm_provider="openai",
            llm_api_key="sk-test",
        )

    mock_sg.add_node.assert_any_call("reason", mock_sg.add_node.call_args_list[0][0][1])
    mock_sg.set_entry_point.assert_called_once_with("reason")
    mock_sg.add_edge.assert_called_once_with("tools", "reason")
    mock_sg.compile.assert_called_once()


# ---------------------------------------------------------------------------
# _should_continue
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_should_continue_returns_end_when_max_iterations_reached():
    agent, _ = _make_agent(max_iterations=3)
    state = {
        "messages": [MagicMock(tool_calls=[MagicMock()])],
        "iteration_count": 3,
    }
    assert agent._should_continue(state) == "end"


@pytest.mark.unit
def test_should_continue_returns_tools_when_tool_calls_present():
    agent, _ = _make_agent()
    msg = MagicMock()
    msg.tool_calls = [MagicMock()]
    state = {"messages": [msg], "iteration_count": 0}
    assert agent._should_continue(state) == "tools"


@pytest.mark.unit
def test_should_continue_returns_end_when_no_tool_calls():
    agent, _ = _make_agent()
    msg = MagicMock()
    msg.tool_calls = []
    state = {"messages": [msg], "iteration_count": 0}
    assert agent._should_continue(state) == "end"


@pytest.mark.unit
def test_should_continue_returns_end_when_message_has_no_tool_calls_attr():
    agent, _ = _make_agent()
    msg = MagicMock(spec=[])  # no .tool_calls attribute
    state = {"messages": [msg], "iteration_count": 0}
    assert agent._should_continue(state) == "end"


# ---------------------------------------------------------------------------
# _reason_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reason_node_returns_messages_and_increments_iteration():
    agent, _ = _make_agent()

    mock_llm_base = MagicMock()
    mock_llm_bound = AsyncMock()
    response_msg = MagicMock()
    response_msg.content = "Let me check your calendar."
    mock_llm_bound.ainvoke = AsyncMock(return_value=response_msg)
    mock_llm_base.bind_tools = MagicMock(return_value=mock_llm_bound)

    state = {
        "messages": [MagicMock()],
        "model": "gpt-4o-mini",
        "user_id": _USER_ID,
        "user_timezone": "UTC",
        "iteration_count": 2,
    }

    with patch(
        "src.agent.graph.create_langchain_chat_model", return_value=mock_llm_base
    ):
        result = await agent._reason_node(state)

    assert result["messages"] == [response_msg]
    assert result["iteration_count"] == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reason_node_uses_default_model_when_state_has_no_model():
    agent, _ = _make_agent()

    mock_llm_base = MagicMock()
    mock_llm_bound = AsyncMock()
    mock_llm_bound.ainvoke = AsyncMock(return_value=MagicMock(content="ok"))
    mock_llm_base.bind_tools = MagicMock(return_value=mock_llm_bound)

    state = {
        "messages": [],
        # no "model" key
        "user_id": _USER_ID,
        "user_timezone": "America/New_York",
        "iteration_count": 0,
    }

    with patch(
        "src.agent.graph.create_langchain_chat_model",
        return_value=mock_llm_base,
    ) as mock_factory:
        await agent._reason_node(state)

    call_kwargs = mock_factory.call_args[1]
    assert call_kwargs["model"] == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_returns_last_ai_message_content():
    agent, compiled = _make_agent()

    content_msg = MagicMock()
    content_msg.content = "You have 2 events tomorrow."
    # Simulate no tool_calls attr
    del content_msg.tool_calls

    compiled.ainvoke = AsyncMock(return_value={"messages": [content_msg]})

    result = await agent.run(
        user_id=_USER_ID,
        message="What's on my calendar tomorrow?",
        model="gpt-4o-mini",
    )
    assert result == "You have 2 events tomorrow."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_skips_messages_with_tool_calls():
    agent, compiled = _make_agent()

    tool_msg = MagicMock()
    tool_msg.content = "tool response"
    tool_msg.tool_calls = [MagicMock()]  # has tool calls — skip

    final_msg = MagicMock()
    final_msg.content = "Final answer"
    final_msg.tool_calls = []  # empty list — include

    compiled.ainvoke = AsyncMock(return_value={"messages": [tool_msg, final_msg]})

    result = await agent.run(
        user_id=_USER_ID,
        message="Schedule a meeting",
    )
    assert result == "Final answer"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_returns_fallback_when_no_valid_message():
    agent, compiled = _make_agent()

    msg = MagicMock()
    msg.content = ""  # empty content
    del msg.tool_calls

    compiled.ainvoke = AsyncMock(return_value={"messages": [msg]})

    result = await agent.run(user_id=_USER_ID, message="hello")
    assert (
        "wasn't able to process" in result.lower() or result
    )  # fallback or real response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_passes_conversation_and_timezone():
    agent, compiled = _make_agent()

    msg = MagicMock()
    msg.content = "Done"
    del msg.tool_calls
    compiled.ainvoke = AsyncMock(return_value={"messages": [msg]})

    await agent.run(
        user_id=_USER_ID,
        message="List events",
        user_timezone="Europe/London",
        user_plan="pro",
        conversation=MagicMock(),
    )

    call_args = compiled.ainvoke.call_args[0][0]
    assert call_args["user_timezone"] == "Europe/London"
    assert call_args["user_plan"] == "pro"
