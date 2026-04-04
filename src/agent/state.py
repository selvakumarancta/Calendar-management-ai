"""
Agent State — defines the state schema for the LangGraph agent.
"""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """State that flows through the LangGraph agent graph."""

    # Conversation messages (LangGraph manages this with add_messages reducer)
    messages: Annotated[list, add_messages]

    # User context
    user_id: str
    user_timezone: str
    user_plan: str

    # Agent control
    model: str
    iteration_count: int
    max_iterations: int

    # Tool results cache (avoid redundant calls within a single run)
    tool_cache: dict[str, Any]

    # Final response
    final_response: str | None
