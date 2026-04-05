"""
LangGraph Agent Graph — the core agent state machine.
Implements the ReAct (Reason + Act) pattern with tool calling.
Provider-agnostic: supports OpenAI, Anthropic, and future LLM providers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from src.agent.prompts import SYSTEM_PROMPT
from src.agent.state import AgentState
from src.agent.tools.calendar_tools import create_calendar_tools
from src.application.services.calendar_service import CalendarService
from src.infrastructure.llm.factory import create_langchain_chat_model


class CalendarAgentGraph:
    """
    LangGraph-based calendar agent.
    Builds a state graph: reason → act (tool call) → observe → respond.
    Provider-agnostic via LLM factory.
    """

    def __init__(
        self,
        calendar_service: CalendarService,
        llm_provider: str,
        llm_api_key: str,
        default_model: str = "",
        max_iterations: int = 10,
        working_hours_start: str = "09:00",
        working_hours_end: str = "17:00",
    ) -> None:
        self._calendar_service = calendar_service
        self._llm_provider = llm_provider
        self._llm_api_key = llm_api_key
        self._default_model = default_model
        self._max_iterations = max_iterations
        self._working_hours_start = working_hours_start
        self._working_hours_end = working_hours_end
        self._tools = create_calendar_tools(calendar_service)
        self._graph = self._build_graph()

    def _build_graph(self) -> Any:
        """Build the LangGraph state machine."""
        graph = StateGraph(AgentState)

        # Add nodes
        graph.add_node("reason", self._reason_node)
        graph.add_node("tools", ToolNode(self._tools))

        # Set entry point
        graph.set_entry_point("reason")

        # Add conditional edges
        graph.add_conditional_edges(
            "reason",
            self._should_continue,
            {
                "tools": "tools",
                "end": END,
            },
        )

        # Tools always go back to reason
        graph.add_edge("tools", "reason")

        return graph.compile()

    async def _reason_node(self, state: AgentState) -> dict:
        """LLM reasoning node — decides next action or final response."""
        model_name = state.get("model", self._default_model)

        # Use factory to get the correct LangChain chat model for the active provider
        llm_base = create_langchain_chat_model(
            provider=self._llm_provider,
            api_key=self._llm_api_key,
            model=model_name,
            temperature=0.1,
        )
        llm = llm_base.bind_tools(self._tools)  # type: ignore[union-attr]

        # Build system message with context — include user_id so LLM always passes it to tools
        user_id = state.get("user_id", "")
        system_msg = SystemMessage(
            content=SYSTEM_PROMPT.format(
                current_date=datetime.now(timezone.utc).strftime("%Y-%m-%d %A"),
                user_timezone=state.get("user_timezone", "UTC"),
                working_hours_start=self._working_hours_start,
                working_hours_end=self._working_hours_end,
                user_id=user_id,
            )
        )

        messages = [system_msg] + state["messages"]
        response = await llm.ainvoke(messages)

        # Track iteration
        iteration = state.get("iteration_count", 0) + 1

        return {
            "messages": [response],
            "iteration_count": iteration,
        }

    def _should_continue(self, state: AgentState) -> str:
        """Decide whether to call tools or end."""
        # Safety: max iterations
        if state.get("iteration_count", 0) >= self._max_iterations:
            return "end"

        # Check if last message has tool calls
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"

        return "end"

    async def run(
        self,
        user_id: str,
        message: str,
        model: str | None = None,
        user_timezone: str = "UTC",
        user_plan: str = "free",
        conversation: Any = None,
    ) -> str:
        """Execute the agent graph with a user message."""
        initial_state: AgentState = {
            "messages": [HumanMessage(content=message)],
            "user_id": str(user_id),
            "user_timezone": user_timezone,
            "user_plan": user_plan,
            "model": model or self._default_model,
            "iteration_count": 0,
            "max_iterations": self._max_iterations,
            "tool_cache": {},
            "final_response": None,
        }

        result = await self._graph.ainvoke(initial_state)

        # Extract final response from last AI message
        for msg in reversed(result["messages"]):
            if (
                hasattr(msg, "content")
                and msg.content
                and not hasattr(msg, "tool_calls")
            ):
                return msg.content
            if (
                hasattr(msg, "content")
                and msg.content
                and hasattr(msg, "tool_calls")
                and not msg.tool_calls
            ):
                return msg.content

        return "I wasn't able to process that request. Could you try rephrasing?"
