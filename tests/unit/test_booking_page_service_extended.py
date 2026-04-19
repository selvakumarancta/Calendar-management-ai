"""
Unit tests for BookingPageService.

All HTTP calls are mocked — no real Calendly/Cal.com API needed.
Covers: platform detection helpers, get_available_slots routing, book_slot,
_http_scrape_slots, _extract_slots_from_nextjs, and utility functions.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.booking_page_service import (
    BookingPageService,
    _add_minutes,
    _detect_platform,
    _extract_calcom_slug,
    _extract_calendly_slug,
    _format_slot_label,
)

# ---------------------------------------------------------------------------
# Platform detection helpers
# ---------------------------------------------------------------------------


class TestDetectPlatform:
    @pytest.mark.unit
    def test_calendly_url(self):
        assert _detect_platform("https://calendly.com/john/30min") == "calendly"

    @pytest.mark.unit
    def test_calcom_url(self):
        assert _detect_platform("https://cal.com/john/30min") == "calcom"

    @pytest.mark.unit
    def test_app_calcom_url(self):
        assert _detect_platform("https://app.cal.com/john/meeting") == "calcom"

    @pytest.mark.unit
    def test_unknown_url(self):
        assert _detect_platform("https://doodle.com/john/30min") == "unknown"


class TestExtractSlugs:
    @pytest.mark.unit
    def test_calendly_two_segment(self):
        u, s = _extract_calendly_slug("https://calendly.com/john-doe/30min")
        assert u == "john-doe"
        assert s == "30min"

    @pytest.mark.unit
    def test_calendly_one_segment(self):
        u, s = _extract_calendly_slug("https://calendly.com/john")
        assert u == "john"
        assert s == ""

    @pytest.mark.unit
    def test_calcom_two_segment(self):
        u, s = _extract_calcom_slug("https://cal.com/alice/team-sync")
        assert u == "alice"
        assert s == "team-sync"

    @pytest.mark.unit
    def test_calcom_one_segment(self):
        u, s = _extract_calcom_slug("https://cal.com/alice")
        assert u == "alice"
        assert s == ""


class TestUtilityHelpers:
    @pytest.mark.unit
    def test_add_minutes_adds_duration(self):
        result = _add_minutes("2026-05-01T10:00:00+00:00", 30)
        assert "10:30" in result

    @pytest.mark.unit
    def test_add_minutes_invalid_returns_original(self):
        result = _add_minutes("not-a-date", 30)
        assert result == "not-a-date"

    @pytest.mark.unit
    def test_format_slot_label_returns_string(self):
        label = _format_slot_label("2026-05-01T10:00:00+00:00", "UTC")
        assert isinstance(label, str)
        assert len(label) > 0

    @pytest.mark.unit
    def test_format_slot_label_invalid_returns_original(self):
        label = _format_slot_label("bad-date", "UTC")
        assert label == "bad-date"


# ---------------------------------------------------------------------------
# get_available_slots routing
# ---------------------------------------------------------------------------


class TestGetAvailableSlots:
    @pytest.mark.unit
    async def test_unknown_url_returns_empty(self):
        svc = BookingPageService()
        result = await svc.get_available_slots("https://doodle.com/poll/xyz")
        assert result == []

    @pytest.mark.unit
    async def test_calendly_no_api_key_uses_scrape(self):
        """Calendly URL without API key falls through to scrape (returns [])."""
        svc = BookingPageService(calendly_api_key="")
        with patch.object(svc, "_http_scrape_slots", new=AsyncMock(return_value=[])):
            result = await svc.get_available_slots("https://calendly.com/john/30min")
        assert result == []

    @pytest.mark.unit
    async def test_calcom_no_api_key_uses_scrape(self):
        """Cal.com URL without API key falls through to scrape."""
        svc = BookingPageService(calcom_api_key="")
        with patch.object(svc, "_http_scrape_slots", new=AsyncMock(return_value=[])):
            result = await svc.get_available_slots("https://cal.com/john/30min")
        assert result == []

    @pytest.mark.unit
    async def test_calcom_with_api_key_calls_api(self):
        """Cal.com URL with API key calls _calcom_api_slots."""
        svc = BookingPageService(calcom_api_key="calcom-key-123")
        fake_slots = [
            {
                "start": "2026-05-01T10:00:00Z",
                "end": "2026-05-01T10:30:00Z",
                "label": "Thu May 1",
            }
        ]
        with patch.object(
            svc, "_calcom_api_slots", new=AsyncMock(return_value=fake_slots)
        ):
            result = await svc.get_available_slots("https://cal.com/john/30min")
        assert result == fake_slots

    @pytest.mark.unit
    async def test_calendly_with_api_key_calls_api(self):
        """Calendly URL with API key calls _calendly_api_slots."""
        svc = BookingPageService(calendly_api_key="cal-key-xyz")
        fake_slots = [
            {
                "start": "2026-05-01T09:00:00Z",
                "end": "2026-05-01T09:30:00Z",
                "label": "Thu May 1",
            }
        ]
        with patch.object(
            svc, "_calendly_api_slots", new=AsyncMock(return_value=fake_slots)
        ):
            result = await svc.get_available_slots("https://calendly.com/john/30min")
        assert result == fake_slots


# ---------------------------------------------------------------------------
# book_slot
# ---------------------------------------------------------------------------


class TestBookSlot:
    @pytest.mark.unit
    async def test_calendly_returns_not_supported(self):
        """Calendly doesn't support programmatic booking."""
        svc = BookingPageService(calendly_api_key="key")
        result = await svc.book_slot(
            url="https://calendly.com/john/30min",
            start_time="2026-05-01T10:00:00Z",
            attendee_name="Alice",
            attendee_email="alice@example.com",
        )
        assert result["success"] is False
        assert (
            "not supported" in result["reason"].lower()
            or "link" in result["reason"].lower()
        )

    @pytest.mark.unit
    async def test_unknown_platform_returns_not_supported(self):
        svc = BookingPageService()
        result = await svc.book_slot(
            url="https://doodle.com/xyz",
            start_time="2026-05-01T10:00:00Z",
            attendee_name="Bob",
            attendee_email="bob@example.com",
        )
        assert result["success"] is False

    @pytest.mark.unit
    async def test_calcom_with_key_calls_api_book(self):
        svc = BookingPageService(calcom_api_key="calcom-key")
        expected = {
            "success": True,
            "confirmation_id": "uid-123",
            "join_url": "https://meet.example.com/xyz",
            "reason": "ok",
        }
        with patch.object(
            svc, "_calcom_api_book", new=AsyncMock(return_value=expected)
        ):
            result = await svc.book_slot(
                url="https://cal.com/john/30min",
                start_time="2026-05-01T10:00:00Z",
                attendee_name="Alice",
                attendee_email="alice@example.com",
                notes="Looking forward to it!",
            )
        assert result["success"] is True
        assert result["confirmation_id"] == "uid-123"


# ---------------------------------------------------------------------------
# _calcom_api_slots (HTTP mocked)
# ---------------------------------------------------------------------------


class TestCalcomApiSlots:
    @pytest.mark.unit
    async def test_returns_slots_on_success(self):
        svc = BookingPageService(calcom_api_key="key-123")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "slots": {
                    "2026-05-01": [
                        {"time": "2026-05-01T09:00:00Z"},
                        {"time": "2026-05-01T09:30:00Z"},
                    ]
                }
            }
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            result = await svc._calcom_api_slots(
                "https://cal.com/john/30min", 30, 7, "UTC"
            )

        assert len(result) == 2
        assert result[0]["start"] == "2026-05-01T09:00:00Z"

    @pytest.mark.unit
    async def test_returns_empty_on_non_200(self):
        svc = BookingPageService(calcom_api_key="key")
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            result = await svc._calcom_api_slots(
                "https://cal.com/john/30min", 30, 7, "UTC"
            )

        assert result == []

    @pytest.mark.unit
    async def test_returns_empty_on_exception(self):
        svc = BookingPageService(calcom_api_key="key")
        with patch("httpx.AsyncClient", side_effect=RuntimeError("network error")):
            result = await svc._calcom_api_slots(
                "https://cal.com/john/30min", 30, 7, "UTC"
            )
        assert result == []

    @pytest.mark.unit
    async def test_empty_slug_still_works(self):
        """URL with just username (no event slug) still makes the API call."""
        svc = BookingPageService(calcom_api_key="key")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"slots": {}}}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            result = await svc._calcom_api_slots("https://cal.com/john", 30, 7, "UTC")

        assert result == []


# ---------------------------------------------------------------------------
# _calcom_api_book (HTTP mocked)
# ---------------------------------------------------------------------------


class TestCalcomApiBook:
    @pytest.mark.unit
    async def test_successful_booking(self):
        svc = BookingPageService(calcom_api_key="key")
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "data": {
                "uid": "booking-uid-123",
                "meetingUrl": "https://meet.example.com/xyz",
            }
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            result = await svc._calcom_api_book(
                url="https://cal.com/john/30min",
                start_time="2026-05-01T10:00:00Z",
                attendee_name="Alice",
                attendee_email="alice@example.com",
                notes="test",
            )

        assert result["success"] is True
        assert result["confirmation_id"] == "booking-uid-123"

    @pytest.mark.unit
    async def test_failed_booking_returns_reason(self):
        svc = BookingPageService(calcom_api_key="key")
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"message": "Slot no longer available"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            result = await svc._calcom_api_book(
                url="https://cal.com/john/30min",
                start_time="2026-05-01T10:00:00Z",
                attendee_name="Alice",
                attendee_email="alice@example.com",
                notes="",
            )

        assert result["success"] is False
        assert "no longer available" in result["reason"]

    @pytest.mark.unit
    async def test_exception_returns_error(self):
        svc = BookingPageService(calcom_api_key="key")
        with patch("httpx.AsyncClient", side_effect=RuntimeError("timeout")):
            result = await svc._calcom_api_book(
                "https://cal.com/john/30min", "2026-05-01T10:00:00Z", "A", "a@b.com", ""
            )
        assert result["success"] is False
        assert "timeout" in result["reason"]


# ---------------------------------------------------------------------------
# _http_scrape_slots
# ---------------------------------------------------------------------------


class TestHttpScrapeSlots:
    @pytest.mark.unit
    async def test_returns_empty_on_non_200(self):
        svc = BookingPageService()
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            result = await svc._http_scrape_slots("https://calendly.com/john/30min", 7)

        assert result == []

    @pytest.mark.unit
    async def test_returns_empty_on_exception(self):
        svc = BookingPageService()
        with patch("httpx.AsyncClient", side_effect=RuntimeError("network error")):
            result = await svc._http_scrape_slots("https://calendly.com/john/30min", 7)
        assert result == []

    @pytest.mark.unit
    async def test_parses_nextjs_data(self):
        svc = BookingPageService()
        next_data = {
            "props": {
                "pageProps": {
                    "availability": [
                        {
                            "start_time": "2026-05-01T09:00:00Z",
                            "end_time": "2026-05-01T09:30:00Z",
                        },
                    ]
                }
            }
        }
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            result = await svc._http_scrape_slots("https://calendly.com/john/30min", 7)

        assert len(result) >= 1
        assert result[0]["start"] == "2026-05-01T09:00:00Z"


# ---------------------------------------------------------------------------
# _extract_slots_from_nextjs (static)
# ---------------------------------------------------------------------------


class TestExtractSlotsFromNextjs:
    @pytest.mark.unit
    def test_extracts_start_time_end_time_format(self):
        data = {
            "slots": [
                {
                    "start_time": "2026-05-01T09:00:00Z",
                    "end_time": "2026-05-01T09:30:00Z",
                },
            ]
        }
        slots = BookingPageService._extract_slots_from_nextjs(data)
        assert len(slots) == 1
        assert slots[0]["start"] == "2026-05-01T09:00:00Z"

    @pytest.mark.unit
    def test_extracts_time_format(self):
        data = {
            "availableSlots": [
                {"time": "2026-05-01T10:00:00Z"},
                {"time": "2026-05-01T10:30:00Z"},
            ]
        }
        slots = BookingPageService._extract_slots_from_nextjs(data)
        assert len(slots) == 2

    @pytest.mark.unit
    def test_empty_data_returns_empty(self):
        slots = BookingPageService._extract_slots_from_nextjs({})
        assert slots == []

    @pytest.mark.unit
    def test_nested_data_is_walked(self):
        data = {
            "page": {
                "props": {
                    "slots": [
                        {"time": "2026-05-01T09:00:00Z"},
                    ]
                }
            }
        }
        slots = BookingPageService._extract_slots_from_nextjs(data)
        assert len(slots) >= 1

    @pytest.mark.unit
    def test_list_items_are_walked(self):
        data = [
            {"time": "2026-05-01T09:00:00Z"},
            {"time": "2026-05-01T09:30:00Z"},
        ]
        # _extract_slots_from_nextjs expects a dict, so wrap it
        slots = BookingPageService._extract_slots_from_nextjs({"items": data})
        assert len(slots) >= 1


# ---------------------------------------------------------------------------
# Slug helpers — empty-path branch (lines 64, 77)
# ---------------------------------------------------------------------------


class TestExtractSlugEmptyPath:
    @pytest.mark.unit
    def test_calendly_slug_returns_empty_when_no_path(self):
        # URL with empty path → len(path)==1 after split → ("", "")? Let's check
        # "https://calendly.com/" → path="" → split("/") → [""] → len==1 covers line 63-64
        result = _extract_calendly_slug("https://calendly.com/")
        assert result == ("", "")

    @pytest.mark.unit
    def test_calcom_slug_returns_empty_when_no_path(self):
        result = _extract_calcom_slug("https://cal.com/")
        assert result == ("", "")


# ---------------------------------------------------------------------------
# _calendly_api_slots / _calcom_api_slots early-return when no slug (lines 192, 288)
# ---------------------------------------------------------------------------


def _make_svc():
    return BookingPageService(
        calendly_api_key="cly_key",
        calcom_api_key="calcom_key",
    )


class TestApiSlotsEmptySlug:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_calendly_api_slots_returns_empty_when_no_slug(self):
        """Empty-path Calendly URL → no username → early return [] (line 192)."""
        svc = _make_svc()
        result = await svc._calendly_api_slots("https://calendly.com/", 7, "UTC")
        assert result == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_calcom_api_slots_returns_empty_when_no_username(self):
        """Empty-path Cal.com URL → no username → early return [] (line 288)."""
        svc = _make_svc()
        result = await svc._calcom_api_slots("https://cal.com/", 30, 7, "UTC")
        assert result == []
