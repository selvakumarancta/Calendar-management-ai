"""
Provider-Aware Calendar Adapter — fetches real events from Google/Microsoft
when real OAuth tokens exist in provider_connections, otherwise falls back
to the in-memory store for dev/demo usage.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from uuid import UUID

from src.domain.entities.calendar_event import CalendarEvent
from src.domain.interfaces.calendar_provider import CalendarProviderPort
from src.domain.interfaces.event_repository import EventRepositoryPort
from src.domain.value_objects import TimeSlot
from src.infrastructure.calendar_providers.in_memory_calendar import (
    InMemoryCalendarAdapter,
)

logger = logging.getLogger("calendar_agent")


class ProviderAwareCalendarAdapter(CalendarProviderPort, EventRepositoryPort):
    """
    Hybrid calendar adapter:
    - When a user has a real Google/Microsoft OAuth connection in the DB
      (access_token != 'dev-token'), delegates to the real API adapter.
    - Otherwise, falls back to an in-memory store.

    Token lookup is done per-request via the _get_provider_tokens callback,
    which is wired at construction time to query provider_connections.
    """

    def __init__(
        self,
        google_client_id: str = "",
        google_client_secret: str = "",
    ) -> None:
        self._google_client_id = google_client_id
        self._google_client_secret = google_client_secret
        self._in_memory = InMemoryCalendarAdapter()
        # Lazily-loaded DB session factory (set by container)
        self._db_session_factory = None

    def set_db_session_factory(self, factory: object) -> None:
        """Set the async session factory for DB token lookups."""
        self._db_session_factory = factory
        # Also give the in-memory adapter DB access for persistence
        self._in_memory.set_db_session_factory(factory)

    async def _get_google_tokens(self, user_id: UUID) -> dict | None:
        """Look up real Google tokens from provider_connections for this user.

        Refreshes the access token only when it is expired or about to expire
        (within 5 minutes). Prefers the newest connection row. Persists the
        refreshed token and updated expiry back to the database.
        """
        if not self._db_session_factory:
            return None
        try:
            from datetime import timezone as _tz

            from sqlalchemy import select

            from src.infrastructure.persistence.org_models import (
                ProviderConnectionModel,
            )

            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(ProviderConnectionModel)
                    .where(
                        ProviderConnectionModel.user_id == user_id,
                        ProviderConnectionModel.provider == "google",
                        ProviderConnectionModel.status == "active",
                        ProviderConnectionModel.access_token != "dev-token",
                    )
                    .order_by(ProviderConnectionModel.created_at.desc())
                )
                all_rows = result.scalars().all()

                # Prefer rows that explicitly list the calendar scope so that
                # a Gmail-only login token doesn't shadow a full-scope connection.
                CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
                rows_with_cal = [
                    r for r in all_rows if CALENDAR_SCOPE in (r.scopes or "")
                ]
                rows = rows_with_cal if rows_with_cal else all_rows

                from src.infrastructure.security.token_encryption import (
                    decrypt_token,
                    encrypt_token,
                )

                for row in rows:
                    if not row.access_token or row.access_token == "dev-token":
                        continue

                    access = decrypt_token(row.access_token)
                    refresh = decrypt_token(row.refresh_token or "")

                    # Skip rows where decryption silently failed (key mismatch)
                    if not access:
                        logger.warning(
                            "Skipping undecryptable token row for user %s (key mismatch?)",
                            user_id,
                        )
                        continue

                    # Refresh when expired or expiring within 15 minutes (was 5).
                    # Wider window means the token is always fresh before it's
                    # needed for create_event, even if the previous calendar load
                    # happened to be 14 minutes before expiry.
                    # Also refresh when token_expiry is None (legacy rows).
                    now_utc = datetime.now(_tz.utc).replace(tzinfo=None)
                    token_expiry = row.token_expiry  # naive UTC from DB
                    needs_refresh = (
                        refresh
                        and self._google_client_id
                        and (
                            token_expiry is None
                            or (token_expiry - now_utc).total_seconds() < 900
                        )
                    )

                    if needs_refresh:
                        try:
                            import httpx

                            # Use httpx directly (async, avoids macOS thread DNS issues
                            # that affect google-auth's blocking requests.Request()).
                            _refresh_tok = refresh
                            async with httpx.AsyncClient() as _hc:
                                _resp = await _hc.post(
                                    "https://oauth2.googleapis.com/token",
                                    data={
                                        "client_id": self._google_client_id,
                                        "client_secret": self._google_client_secret,
                                        "refresh_token": _refresh_tok,
                                        "grant_type": "refresh_token",
                                    },
                                    timeout=15.0,
                                )
                            if _resp.status_code == 200:
                                _tok = _resp.json()
                                new_access = _tok["access_token"]
                                new_refresh = _tok.get("refresh_token") or refresh
                                # Parse expiry (seconds from now)
                                import datetime as _dt
                                _expires_in = _tok.get("expires_in", 3600)
                                new_expiry = (
                                    _dt.datetime.now(_tz.utc)
                                    + _dt.timedelta(seconds=_expires_in)
                                ).replace(tzinfo=None)
                                row.access_token = encrypt_token(new_access)
                                row.refresh_token = encrypt_token(new_refresh)
                                row.token_expiry = new_expiry
                                row.updated_at = datetime.now(_tz.utc).replace(
                                    tzinfo=None
                                )
                                await session.commit()
                                access = new_access
                                refresh = new_refresh
                                logger.info(
                                    "Refreshed Google Calendar token for user %s (expires %s)",
                                    user_id,
                                    new_expiry,
                                )
                            else:
                                _err = _resp.json().get("error", "")
                                if _err == "invalid_client":
                                    logger.error(
                                        "⚠️  GOOGLE_CLIENT_SECRET is invalid for user %s. "
                                        "Go to Google Cloud Console → APIs & Services → "
                                        "Credentials and update GOOGLE_CLIENT_SECRET in .env",
                                        user_id,
                                    )
                                else:
                                    logger.warning(
                                        "Calendar token refresh failed for user %s: HTTP %s %s",
                                        user_id,
                                        _resp.status_code,
                                        _resp.text[:200],
                                    )
                                continue
                        except Exception as ref_err:
                            logger.warning(
                                "Calendar token refresh failed for user %s: %s — "
                                "trying next row",
                                user_id,
                                ref_err,
                            )
                            # Don't return a potentially expired token; try next row
                            continue

                    return {
                        "access_token": access,
                        "refresh_token": refresh,
                        "provider_email": row.provider_email,
                    }
        except Exception as e:
            logger.warning("Failed to look up Google tokens: %s", e)
        return None

    def _build_google_service(self, tokens: dict):  # type: ignore[no-untyped-def]
        """Build an authorized Google Calendar API service from tokens.

        google-auth will automatically refresh the access token on the first
        API call if it is expired, as long as refresh_token and client
        credentials are present.
        """
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials(
            token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token") or None,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._google_client_id,
            client_secret=self._google_client_secret,
        )
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    # ---- CalendarProviderPort -----------------------------------------

    async def list_events(
        self,
        user_id: UUID,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        """List events — merge real Google Calendar events with locally-created events."""
        tokens = await self._get_google_tokens(user_id)
        google_events: list[CalendarEvent] = []
        if tokens:
            try:
                google_events = await self._list_google_events(
                    tokens, user_id, start, end, calendar_id, max_results
                )
            except Exception as e:
                err_str = str(e)
                is_auth_err = (
                    "401" in err_str
                    or "invalid_grant" in err_str
                    or "Token has been expired" in err_str
                    or "unauthorized" in err_str.lower()
                )
                if is_auth_err:
                    # Token silently expired — force refresh and retry once
                    logger.warning(
                        "list_events: auth error for user %s — force-refreshing token",
                        user_id,
                    )
                    fresh = await self._force_refresh_tokens(user_id)
                    if fresh:
                        try:
                            google_events = await self._list_google_events(
                                fresh, user_id, start, end, calendar_id, max_results
                            )
                        except Exception as retry_e:
                            logger.warning(
                                "list_events retry failed for user %s: %s",
                                user_id,
                                retry_e,
                            )
                elif "insufficientPermissions" in err_str or "403" in err_str:
                    logger.warning(
                        "Google Calendar API: insufficient scope for user %s — "
                        "user needs to reconnect Google with Calendar permission. "
                        "Error: %s",
                        user_id,
                        e,
                    )
                else:
                    logger.warning(
                        "Google Calendar API failed, falling back to in-memory: %s", e
                    )

        # Always also fetch locally-created events (those created via the app)
        # so they're visible regardless of whether the Google API call succeeded.
        local_events = await self._in_memory.list_events(
            user_id, start, end, calendar_id, max_results
        )

        if not google_events:
            return local_events

        # Merge: prefer Google Calendar events; add local events that aren't
        # already represented (identified by provider_event_id).
        google_ids = {e.provider_event_id for e in google_events if e.provider_event_id}
        extra_local = [e for e in local_events if e.provider_event_id not in google_ids]
        return google_events + extra_local

    async def _list_google_events(
        self,
        tokens: dict,
        user_id: UUID,
        start: datetime,
        end: datetime,
        calendar_id: str,
        max_results: int,
    ) -> list[CalendarEvent]:
        """Fetch events from Google Calendar API."""
        import asyncio

        from src.infrastructure.calendar_providers.google_calendar import (
            GoogleCalendarAdapter,
        )

        service = self._build_google_service(tokens)

        # Ensure timezone-aware ISO format
        from datetime import timezone as _tz

        def _to_utc(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=_tz.utc)
            return dt.astimezone(_tz.utc)

        start_utc = _to_utc(start)
        end_utc = _to_utc(end)
        start_iso = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        def _do_list() -> dict:
            return (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=start_iso,
                    timeMax=end_iso,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

        result = await asyncio.get_event_loop().run_in_executor(None, _do_list)

        events = []
        for item in result.get("items", []):
            try:
                events.append(GoogleCalendarAdapter._parse_event(item, user_id))
            except Exception as e:
                logger.warning("Failed to parse event %s: %s", item.get("id"), e)
        return events

    async def get_event(
        self,
        user_id: UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> CalendarEvent | None:
        tokens = await self._get_google_tokens(user_id)
        if tokens:
            try:
                service = self._build_google_service(tokens)
                result = (
                    service.events()
                    .get(calendarId=calendar_id, eventId=event_id)
                    .execute()
                )
                from src.infrastructure.calendar_providers.google_calendar import (
                    GoogleCalendarAdapter,
                )

                return GoogleCalendarAdapter._parse_event(result, user_id)
            except Exception:
                pass
        return await self._in_memory.get_event(user_id, event_id, calendar_id)

    async def _force_refresh_tokens(self, user_id: UUID) -> dict | None:
        """Force-refresh the Google token regardless of expiry, then return fresh tokens.

        Used as a fallback when an API call returns 401/403 — the cached token
        may have already been invalidated on Google's side even if our expiry
        timestamp looks valid (e.g. clock skew, revocation).
        """
        if not self._db_session_factory or not self._google_client_id:
            return None
        try:
            from datetime import timezone as _tz

            from sqlalchemy import select

            from src.infrastructure.persistence.org_models import (
                ProviderConnectionModel,
            )
            from src.infrastructure.security.token_encryption import (
                decrypt_token,
                encrypt_token,
            )

            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(ProviderConnectionModel)
                    .where(
                        ProviderConnectionModel.user_id == user_id,
                        ProviderConnectionModel.provider == "google",
                        ProviderConnectionModel.status == "active",
                        ProviderConnectionModel.access_token != "dev-token",
                    )
                    .order_by(ProviderConnectionModel.created_at.desc())
                )
                row = result.scalars().first()
                if not row:
                    return None

                refresh = decrypt_token(row.refresh_token or "")
                if not refresh:
                    return None

                import httpx
                import datetime as _dt
                from datetime import timezone as _tz2

                # Use httpx directly — avoids macOS thread DNS issues with requests.
                async with httpx.AsyncClient() as _hc:
                    _resp = await _hc.post(
                        "https://oauth2.googleapis.com/token",
                        data={
                            "client_id": self._google_client_id,
                            "client_secret": self._google_client_secret,
                            "refresh_token": refresh,
                            "grant_type": "refresh_token",
                        },
                        timeout=15.0,
                    )

                if _resp.status_code == 200:
                    _tok = _resp.json()
                    new_access = _tok["access_token"]
                    new_refresh = _tok.get("refresh_token") or refresh
                    _expires_in = _tok.get("expires_in", 3600)
                    new_expiry = (
                        _dt.datetime.now(_tz2.utc)
                        + _dt.timedelta(seconds=_expires_in)
                    ).replace(tzinfo=None)
                    row.access_token = encrypt_token(new_access)
                    row.refresh_token = encrypt_token(new_refresh)
                    row.token_expiry = new_expiry
                    row.updated_at = datetime.now(_tz2.utc).replace(tzinfo=None)
                    await session.commit()
                    logger.info(
                        "Force-refreshed Google token for user %s after auth failure",
                        user_id,
                    )
                    return {
                        "access_token": new_access,
                        "refresh_token": new_refresh,
                        "provider_email": row.provider_email,
                    }
                else:
                    _err_body = _resp.json()
                    if _err_body.get("error") == "invalid_client":
                        logger.error(
                            "⚠️  GOOGLE_CLIENT_SECRET is invalid for user %s. "
                            "Go to Google Cloud Console → APIs & Services → "
                            "Credentials and update GOOGLE_CLIENT_SECRET in .env",
                            user_id,
                        )
                    else:
                        logger.warning(
                            "Force token refresh HTTP %s for user %s: %s",
                            _resp.status_code,
                            user_id,
                            _resp.text[:200],
                        )
        except Exception as e:
            logger.warning("Force token refresh failed for user %s: %s", user_id, e)
        return None

    async def create_event(
        self,
        user_id: UUID,
        event: CalendarEvent,
    ) -> CalendarEvent:
        tokens = await self._get_google_tokens(user_id)
        if not tokens:
            logger.warning(
                "create_event: no valid Google tokens for user %s — storing locally only",
                user_id,
            )
            return await self._in_memory.create_event(user_id, event)

        import asyncio

        from src.infrastructure.calendar_providers.google_calendar import (
            GoogleCalendarAdapter,
        )

        body = GoogleCalendarAdapter._to_google_event(event)
        _cal_id = event.calendar_id or "primary"

        # Try with current tokens; on 401/403 force-refresh and retry once.
        for attempt in range(2):
            try:
                service = self._build_google_service(tokens)

                def _do_insert(svc=service) -> dict:  # capture svc per-attempt
                    return svc.events().insert(calendarId=_cal_id, body=body).execute()

                result = await asyncio.get_event_loop().run_in_executor(
                    None, _do_insert
                )
                event.provider_event_id = result["id"]
                # Also persist to local DB so it's visible via list_events fallback
                await self._in_memory.create_event(user_id, event)
                logger.info(
                    "Google Calendar event created for user %s: %s (%s)",
                    user_id,
                    result["id"],
                    event.title,
                )
                return event
            except Exception as e:
                err_str = str(e)
                is_auth_err = (
                    "401" in err_str
                    or "403" in err_str
                    or "invalid_grant" in err_str
                    or "Token has been expired" in err_str
                    or "unauthorized" in err_str.lower()
                )
                if is_auth_err and attempt == 0:
                    logger.warning(
                        "create_event: auth error on attempt 1 for user %s — "
                        "force-refreshing token and retrying. Error: %s",
                        user_id,
                        e,
                    )
                    fresh = await self._force_refresh_tokens(user_id)
                    if fresh:
                        tokens = fresh
                        continue  # retry with fresh token
                # Permanent failure or 2nd attempt
                if "insufficientPermissions" in err_str or "403" in err_str:
                    logger.warning(
                        "Google Calendar create_event: insufficient scope for user %s — "
                        "reconnect Google with Calendar permission. Error: %s",
                        user_id,
                        e,
                    )
                else:
                    logger.error(
                        "Google Calendar create_event FAILED for user %s — "
                        "title=%r error=%s",
                        user_id,
                        event.title,
                        e,
                    )
                break
        return await self._in_memory.create_event(user_id, event)

    async def update_event(
        self,
        user_id: UUID,
        event: CalendarEvent,
    ) -> CalendarEvent:
        """Update event on Google Calendar (if tokens available) and in local DB."""
        tokens = await self._get_google_tokens(user_id)
        if tokens and event.provider_event_id:
            try:
                from src.infrastructure.calendar_providers.google_calendar import (
                    GoogleCalendarAdapter,
                )

                service = self._build_google_service(tokens)
                body = GoogleCalendarAdapter._to_google_event(event)
                service.events().update(
                    calendarId=event.calendar_id or "primary",
                    eventId=event.provider_event_id,
                    body=body,
                ).execute()
                logger.info(
                    "Updated Google Calendar event %s for user %s",
                    event.provider_event_id,
                    user_id,
                )
            except Exception as e:
                logger.warning(
                    "Failed to update Google Calendar event %s: %s",
                    event.provider_event_id,
                    e,
                )
        return await self._in_memory.update_event(user_id, event)

    async def delete_event(
        self,
        user_id: UUID,
        event_id: str,
        calendar_id: str = "primary",
    ) -> bool:
        """Delete event from Google Calendar (if tokens available) and local DB."""
        tokens = await self._get_google_tokens(user_id)
        google_deleted = False
        if tokens and event_id:
            try:
                service = self._build_google_service(tokens)
                service.events().delete(
                    calendarId=calendar_id, eventId=event_id
                ).execute()
                google_deleted = True
                logger.info(
                    "Deleted Google Calendar event %s for user %s", event_id, user_id
                )
            except Exception as e:
                logger.warning(
                    "Failed to delete Google Calendar event %s: %s", event_id, e
                )
        local_deleted = await self._in_memory.delete_event(
            user_id, event_id, calendar_id
        )
        return google_deleted or local_deleted

    async def find_free_slots(
        self,
        user_id: UUID,
        start: datetime,
        end: datetime,
        duration_minutes: int = 30,
        calendar_id: str = "primary",
    ) -> list[TimeSlot]:
        events = await self.list_events(user_id, start, end, calendar_id)
        events.sort(key=lambda e: e.start_time)

        free: list[TimeSlot] = []
        cursor = start
        for ev in events:
            if (ev.start_time - cursor).total_seconds() >= duration_minutes * 60:
                free.append(TimeSlot(start=cursor, end=ev.start_time))
            cursor = max(cursor, ev.end_time)
        if (end - cursor).total_seconds() >= duration_minutes * 60:
            free.append(TimeSlot(start=cursor, end=end))
        return free

    # ---- EventRepositoryPort (delegates to in-memory) ------------------

    async def get_by_id(self, event_id: uuid.UUID) -> CalendarEvent | None:
        return await self._in_memory.get_by_id(event_id)

    async def get_by_provider_id(
        self, provider_event_id: str, user_id: UUID
    ) -> CalendarEvent | None:
        return await self._in_memory.get_by_provider_id(provider_event_id, user_id)

    async def list_by_user(
        self,
        user_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        return await self._in_memory.list_by_user(user_id, start, end, limit)

    async def create(self, event: CalendarEvent) -> CalendarEvent:
        return await self._in_memory.create(event)

    async def update(self, event: CalendarEvent) -> CalendarEvent:
        return await self._in_memory.update(event)

    async def delete(self, event_id: uuid.UUID) -> bool:
        return await self._in_memory.delete(event_id)

    # ---- Scheduling calendar helpers -----------------------------------

    async def get_or_create_scheduling_calendar(
        self,
        user_id: UUID,
        calendar_name: str = "CalendarAgent",
    ) -> str:
        """
        Return the Google Calendar ID for the dedicated scheduling calendar.

        If a calendar named ``calendar_name`` already exists it is reused.
        Otherwise a new secondary calendar is created on behalf of the user.
        Falls back to "primary" when no real Google tokens are available.
        """
        tokens = await self._get_google_tokens(user_id)
        if not tokens:
            return "primary"

        try:
            service = self._build_google_service(tokens)

            # 1. Check if the calendar already exists
            cal_list = service.calendarList().list().execute()
            for entry in cal_list.get("items", []):
                if entry.get("summary") == calendar_name:
                    return entry["id"]

            # 2. Create a new secondary calendar
            new_cal = (
                service.calendars()
                .insert(
                    body={
                        "summary": calendar_name,
                        "description": "Managed by CalendarAgent",
                    }
                )
                .execute()
            )
            cal_id: str = new_cal["id"]
            logger.info(
                "Created dedicated scheduling calendar '%s' (id=%s) for user %s",
                calendar_name,
                cal_id,
                user_id,
            )
            return cal_id

        except Exception as e:
            logger.warning(
                "Could not get/create scheduling calendar for user %s: %s — using primary",
                user_id,
                e,
            )
            return "primary"

    async def persist_scheduling_calendar_id(
        self, user_id: UUID, calendar_id: str
    ) -> None:
        """Persists the scheduling_calendar_id to the user record in DB."""
        if not self._db_session_factory:
            return
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.models import UserModel

            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(UserModel).where(UserModel.id == user_id)
                )
                user_row = result.scalars().first()
                if user_row:
                    user_row.scheduling_calendar_id = calendar_id
                    await session.commit()
        except Exception as e:
            logger.warning(
                "Could not persist scheduling_calendar_id for user %s: %s", user_id, e
            )
