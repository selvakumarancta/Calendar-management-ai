"""
Intent Router — classifies user requests for cost-optimized routing.
Combines rule-based pattern matching (zero LLM cost) with fallback classification.
"""

from __future__ import annotations

import re

from src.application.dto import RequestComplexity

# Patterns for deterministic shortcuts (no LLM needed)
DETERMINISTIC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"\b(what('?s)?|show|list|get)\b.*\b(today|tomorrow|schedule|agenda|calendar)\b",
            re.I,
        ),
        "list_today",
    ),
    (
        re.compile(r"\b(what('?s)?)\b.*\b(next meeting|next event)\b", re.I),
        "next_event",
    ),
    (
        re.compile(r"\bdelete\b.*\b(my|the)\b.*\b(meeting|event)\b", re.I),
        "delete_event",
    ),
    (re.compile(r"\bremind\b.*\b(\d+)\s*(min|minute|hour)\b", re.I), "set_reminder"),
]

# Patterns for simple requests (use fast/cheap model)
SIMPLE_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\b(create|add|schedule|book)\b.*\b(meeting|event|call|appointment)\b", re.I
    ),
    re.compile(r"\b(when|what time)\b.*\b(free|available|open)\b", re.I),
    re.compile(r"\b(cancel|remove)\b.*\b(event|meeting)\b", re.I),
    re.compile(r"\b(move|change|update)\b.*\b(event|meeting|time)\b", re.I),
]

# Patterns indicating complex reasoning
COMPLEX_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\b(reorganize|rearrange|optimize)\b.*\b(schedule|calendar|week)\b", re.I
    ),
    re.compile(
        r"\b(find|suggest)\b.*\b(best time|optimal)\b.*\b(everyone|all|team)\b", re.I
    ),
    re.compile(
        r"\b(recurring|every|weekly|daily|monthly)\b.*\b(schedule|setup|create)\b", re.I
    ),
    re.compile(r"\b(resolve|fix|handle)\b.*\b(conflicts?|overlaps?)\b", re.I),
    re.compile(r"\b(conflicts?|overlaps?)\b.*\b(resolve|fix|handle)\b", re.I),
]


class IntentRouter:
    """
    Rule-based intent classifier for cost-optimized request routing.
    No LLM calls — purely pattern-matching (zero cost).
    """

    def classify(self, message: str) -> RequestComplexity:
        """Classify a user message into a complexity tier."""
        # Check deterministic patterns first (cheapest — no LLM)
        for pattern, _action in DETERMINISTIC_PATTERNS:
            if pattern.search(message):
                return RequestComplexity.DETERMINISTIC

        # Check complex patterns (most expensive model)
        for pattern in COMPLEX_PATTERNS:
            if pattern.search(message):
                return RequestComplexity.COMPLEX

        # Check simple patterns (fast/cheap model)
        for pattern in SIMPLE_PATTERNS:
            if pattern.search(message):
                return RequestComplexity.SIMPLE

        # Default: medium complexity
        return RequestComplexity.MEDIUM

    def get_deterministic_action(self, message: str) -> str | None:
        """Return the deterministic action identifier, or None."""
        for pattern, action in DETERMINISTIC_PATTERNS:
            if pattern.search(message):
                return action
        return None
