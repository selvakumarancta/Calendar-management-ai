"""
Integration tests for calendar event CRUD endpoints added in session 8/9:
  - POST   /api/v1/calendar/events          (create)
  - PATCH  /api/v1/calendar/events/{id}     (update)
  - DELETE /api/v1/calendar/events/{id}     (delete)

The dev-login user has no real OAuth provider, so the in-memory calendar
adapter is used — responses are deterministic and require no external tokens.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

# Unique base time per test-run to avoid conflicts in the persistent dev DB.
# Picks a random slot within [2040, 2140] at 1-hour granularity.
_RUN_OFFSET_H = uuid.uuid4().int % (365 * 24 * 100)
_BASE = datetime(2040, 1, 1, tzinfo=timezone.utc) + timedelta(hours=_RUN_OFFSET_H)


def _dt(offset_hours: int = 0) -> str:
    """Return an ISO-8601 string for _BASE + offset_hours."""
    return (_BASE + timedelta(hours=offset_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def app():
    from src.api.rest.app import create_app
    from src.config.container import Container
    from src.config.settings import Settings
    from src.infrastructure.security.token_encryption import set_encryption_key

    application = create_app()
    settings = Settings()
    set_encryption_key(settings.app_secret_key)
    container = Container(settings)
    await container.database().create_tables()
    application.state.container = container
    yield application
    await container.shutdown()


@pytest.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
async def auth_headers(client):
    resp = await client.post("/api/v1/auth/dev-login")
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
async def created_event(client, auth_headers):
    """Helper: create a calendar event and return its JSON. Cleans up after the test."""
    # Use _dt(100+) — well beyond the slots used by TestCreateCalendarEvent (0-8).
    slot = 100 + random.randint(0, 50_000)
    payload = {
        "title": "Test Meeting",
        "start_time": _dt(slot),
        "end_time": _dt(slot + 1),
        "calendar_id": "primary",
    }
    resp = await client.post(
        "/api/v1/calendar/events", json=payload, headers=auth_headers
    )
    assert resp.status_code == 201, resp.text
    event = resp.json()
    yield event
    # Teardown: remove the event so it doesn't pollute subsequent test runs.
    await client.delete(f"/api/v1/calendar/events/{event['id']}", headers=auth_headers)


# ---------------------------------------------------------------------------
# POST /api/v1/calendar/events
# ---------------------------------------------------------------------------


class TestCreateCalendarEvent:
    @pytest.mark.integration
    async def test_create_event_returns_201(self, client, auth_headers):
        payload = {
            "title": "Sprint Planning",
            "start_time": _dt(0),
            "end_time": _dt(1),
        }
        resp = await client.post(
            "/api/v1/calendar/events", json=payload, headers=auth_headers
        )
        assert resp.status_code == 201, resp.text

    @pytest.mark.integration
    async def test_create_event_response_has_required_fields(
        self, client, auth_headers
    ):
        payload = {
            "title": "All Hands",
            "start_time": _dt(2),
            "end_time": _dt(3),
        }
        resp = await client.post(
            "/api/v1/calendar/events", json=payload, headers=auth_headers
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "All Hands"
        assert "id" in data
        assert "start_time" in data
        assert "end_time" in data

    @pytest.mark.integration
    async def test_create_event_requires_auth(self, client):
        payload = {
            "title": "No Auth",
            "start_time": "2035-06-01T10:00:00Z",
            "end_time": "2035-06-01T11:00:00Z",
        }
        resp = await client.post("/api/v1/calendar/events", json=payload)
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_create_event_missing_title_422(self, client, auth_headers):
        payload = {
            "start_time": _dt(4),
            "end_time": _dt(5),
        }
        resp = await client.post(
            "/api/v1/calendar/events", json=payload, headers=auth_headers
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    async def test_create_event_with_attendees(self, client, auth_headers):
        payload = {
            "title": "Team Sync",
            "start_time": _dt(6),
            "end_time": _dt(7),
            "attendee_emails": ["alice@example.com", "bob@example.com"],
            "location": "Zoom",
            "description": "Weekly team meeting",
        }
        resp = await client.post(
            "/api/v1/calendar/events", json=payload, headers=auth_headers
        )
        assert resp.status_code == 201

    @pytest.mark.integration
    async def test_create_all_day_event(self, client, auth_headers):
        payload = {
            "title": "Company Holiday",
            "start_time": _dt(8),
            "end_time": _dt(8),
            "is_all_day": True,
        }
        resp = await client.post(
            "/api/v1/calendar/events", json=payload, headers=auth_headers
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# PATCH /api/v1/calendar/events/{event_id}
# ---------------------------------------------------------------------------


class TestUpdateCalendarEvent:
    @pytest.mark.integration
    async def test_update_event_title_returns_200(
        self, client, auth_headers, created_event
    ):
        event_id = created_event["id"]
        resp = await client.patch(
            f"/api/v1/calendar/events/{event_id}",
            json={"title": "Updated Title"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.integration
    async def test_update_event_reflects_new_title(
        self, client, auth_headers, created_event
    ):
        event_id = created_event["id"]
        resp = await client.patch(
            f"/api/v1/calendar/events/{event_id}",
            json={"title": "Renamed Event"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Renamed Event"

    @pytest.mark.integration
    async def test_update_event_location_and_description(
        self, client, auth_headers, created_event
    ):
        event_id = created_event["id"]
        resp = await client.patch(
            f"/api/v1/calendar/events/{event_id}",
            json={
                "title": created_event["title"],
                "location": "Conference Room B",
                "description": "Agenda: Q3 review",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200

    @pytest.mark.integration
    async def test_update_event_reschedule(self, client, auth_headers, created_event):
        event_id = created_event["id"]
        resp = await client.patch(
            f"/api/v1/calendar/events/{event_id}",
            json={
                "title": created_event["title"],
                "start_time": _dt(200),
                "end_time": _dt(201),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200

    @pytest.mark.integration
    async def test_update_nonexistent_event_404(self, client, auth_headers):
        resp = await client.patch(
            "/api/v1/calendar/events/nonexistent-event-id-xyz",
            json={"title": "Ghost Event"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    async def test_update_event_requires_auth(self, client, created_event):
        event_id = created_event["id"]
        resp = await client.patch(
            f"/api/v1/calendar/events/{event_id}",
            json={"title": "No Auth Update"},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_update_event_with_attendees(
        self, client, auth_headers, created_event
    ):
        event_id = created_event["id"]
        resp = await client.patch(
            f"/api/v1/calendar/events/{event_id}",
            json={
                "title": created_event["title"],
                "attendee_emails": ["charlie@example.com"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# DELETE /api/v1/calendar/events/{event_id}
# ---------------------------------------------------------------------------


class TestDeleteCalendarEvent:
    @pytest.mark.integration
    async def test_delete_event_returns_204(self, client, auth_headers, created_event):
        event_id = created_event["id"]
        resp = await client.delete(
            f"/api/v1/calendar/events/{event_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    @pytest.mark.integration
    async def test_delete_nonexistent_event_404(self, client, auth_headers):
        resp = await client.delete(
            "/api/v1/calendar/events/does-not-exist-abc123",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    async def test_delete_event_requires_auth(self, client, created_event):
        event_id = created_event["id"]
        resp = await client.delete(f"/api/v1/calendar/events/{event_id}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/calendar/events (list)
# ---------------------------------------------------------------------------


class TestListCalendarEvents:
    @pytest.mark.integration
    async def test_list_events_returns_200(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/calendar/events",
            params={"start": "2026-05-01T00:00:00Z", "end": "2026-05-31T23:59:59Z"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.integration
    async def test_list_events_requires_auth(self, client):
        resp = await client.get(
            "/api/v1/calendar/events",
            params={"start": "2026-05-01T00:00:00Z", "end": "2026-05-31T23:59:59Z"},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    async def test_list_events_missing_params_422(self, client, auth_headers):
        resp = await client.get("/api/v1/calendar/events", headers=auth_headers)
        assert resp.status_code == 422
