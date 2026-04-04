"""
Tests for Intent Router — verifies correct complexity classification.
"""

from __future__ import annotations

import pytest

from src.agent.router import IntentRouter
from src.application.dto import RequestComplexity


class TestIntentRouter:
    """Test the rule-based intent classification."""

    def setup_method(self) -> None:
        self.router = IntentRouter()

    @pytest.mark.unit
    def test_deterministic_todays_agenda(self) -> None:
        assert (
            self.router.classify("What's on my calendar today?")
            == RequestComplexity.DETERMINISTIC
        )

    @pytest.mark.unit
    def test_deterministic_show_schedule(self) -> None:
        assert (
            self.router.classify("Show my schedule for tomorrow")
            == RequestComplexity.DETERMINISTIC
        )

    @pytest.mark.unit
    def test_simple_create_meeting(self) -> None:
        assert (
            self.router.classify("Schedule a meeting with John at 3pm")
            == RequestComplexity.SIMPLE
        )

    @pytest.mark.unit
    def test_simple_find_free_time(self) -> None:
        assert (
            self.router.classify("When am I free this week?")
            == RequestComplexity.SIMPLE
        )

    @pytest.mark.unit
    def test_complex_reorganize(self) -> None:
        assert (
            self.router.classify("Reorganize my entire schedule for next week")
            == RequestComplexity.COMPLEX
        )

    @pytest.mark.unit
    def test_complex_resolve_conflicts(self) -> None:
        assert (
            self.router.classify("Resolve all the conflicts in my calendar")
            == RequestComplexity.COMPLEX
        )

    @pytest.mark.unit
    def test_medium_default(self) -> None:
        assert (
            self.router.classify("Can you help me with something?")
            == RequestComplexity.MEDIUM
        )

    @pytest.mark.unit
    def test_deterministic_action_detected(self) -> None:
        action = self.router.get_deterministic_action("What's on my calendar today?")
        assert action == "list_today"

    @pytest.mark.unit
    def test_no_deterministic_action_for_complex(self) -> None:
        action = self.router.get_deterministic_action("Reorganize my week")
        assert action is None
