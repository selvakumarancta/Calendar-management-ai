"""
Unit tests for agent state and prompts modules.
These are purely structural — importing them and checking shape.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_agent_state_has_expected_keys():
    from src.agent.state import AgentState

    annotations = AgentState.__annotations__
    assert "messages" in annotations
    assert "user_id" in annotations
    assert "user_timezone" in annotations
    assert "user_plan" in annotations
    assert "model" in annotations
    assert "iteration_count" in annotations
    assert "max_iterations" in annotations
    assert "tool_cache" in annotations
    assert "final_response" in annotations


@pytest.mark.unit
def test_agent_state_is_typed_dict():
    from typing_extensions import TypedDict

    from src.agent.state import AgentState

    assert issubclass(AgentState, dict)


@pytest.mark.unit
def test_agent_prompts_importable():
    """All prompt constants in prompts.py must be importable strings."""
    import src.agent.prompts as prompts_mod

    for name in dir(prompts_mod):
        if name.startswith("_"):
            continue
        val = getattr(prompts_mod, name)
        if isinstance(val, str):
            assert len(val) > 0, f"Prompt constant {name!r} is empty"
