"""
Chat Service — orchestrates the full agent interaction pipeline.
This is the main entry point for user messages. It handles:
  1. Quota checking
  2. Intent routing (deterministic shortcut vs. agent)
  3. Caching (semantic + response)
  4. Model selection (complexity routing)
  5. Agent execution
  6. Usage tracking
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from src.application.dto import ChatRequestDTO, ChatResponseDTO, RequestComplexity
from src.domain.entities.calendar_event import CalendarEvent, EventStatus
from src.domain.entities.conversation import Conversation, MessageRole
from src.domain.exceptions import QuotaExceededError
from src.domain.interfaces.cache import CachePort
from src.domain.interfaces.calendar_provider import CalendarProviderPort
from src.domain.interfaces.conversation_repository import ConversationRepositoryPort
from src.domain.interfaces.usage_tracker import UsageTrackerPort


class ChatService:
    """
    Main SaaS chat orchestrator — the entry point for all user interactions.
    Coordinates quota, routing, caching, agent execution, and billing.
    """

    def __init__(
        self,
        conversation_repo: ConversationRepositoryPort,
        usage_tracker: UsageTrackerPort,
        cache: CachePort,
        # Agent and router are injected but typed loosely here
        # to avoid circular dependency with agent module
        agent_executor: object,
        intent_router: object,
        complexity_router: object,
        # Calendar provider for creating real events from chat
        calendar_provider: CalendarProviderPort | None = None,
        # LLM provider config for model selection
        llm_provider: str = "anthropic",
        model_fast: str = "claude-haiku-3-20250414",
        model_primary: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._conversation_repo = conversation_repo
        self._usage_tracker = usage_tracker
        self._cache = cache
        self._agent_executor = agent_executor
        self._intent_router = intent_router
        self._complexity_router = complexity_router
        self._calendar_provider = calendar_provider
        self._llm_provider = llm_provider
        self._model_fast = model_fast
        self._model_primary = model_primary

    async def handle_message(
        self, user_id: UUID, request: ChatRequestDTO, plan_limit: int
    ) -> ChatResponseDTO:
        """
        Process a user message through the full pipeline:
        quota → cache → route → execute → track → respond.
        """
        # 1. Check quota
        within_quota = await self._usage_tracker.is_within_quota(user_id, plan_limit)
        if not within_quota:
            raise QuotaExceededError(limit=plan_limit)

        # 2. Get or create conversation
        conversation = await self._get_or_create_conversation(
            user_id, request.conversation_id
        )
        conversation.add_message(MessageRole.USER, request.message)

        # 3. Check semantic cache
        cache_key = f"chat:{user_id}:{hash(request.message)}"
        cached_response = await self._cache.get(cache_key)
        if cached_response is not None:
            return ChatResponseDTO(
                message=cached_response,
                conversation_id=conversation.id,
            )

        # 4. Route: deterministic shortcut vs. agent
        complexity = self._classify_request(request.message)

        if complexity == RequestComplexity.DETERMINISTIC:
            response_text = await self._handle_deterministic(user_id, request.message)
        else:
            # 5. Select model based on complexity
            model = self._select_model(complexity)
            response_text = await self._execute_agent(
                user_id, conversation, request.message, model
            )

        # 6. Store response in conversation
        conversation.add_message(MessageRole.ASSISTANT, response_text)
        await self._conversation_repo.update(conversation)

        # 7. Track usage
        await self._usage_tracker.record_request(user_id)

        # 8. Cache response
        await self._cache.set(cache_key, response_text, ttl_seconds=300)

        return ChatResponseDTO(
            message=response_text,
            conversation_id=conversation.id,
        )

    async def _get_or_create_conversation(
        self, user_id: UUID, conversation_id: UUID | None
    ) -> Conversation:
        """Retrieve existing or start new conversation."""
        if conversation_id:
            conv = await self._conversation_repo.get_by_id(conversation_id)
            if conv:
                return conv

        conv = Conversation(id=uuid4(), user_id=user_id)
        return await self._conversation_repo.create(conv)

    def _classify_request(self, message: str) -> RequestComplexity:
        """Classify request complexity via intent router."""
        # Delegate to the injected intent router
        # This will be implemented in the agent module
        if hasattr(self._intent_router, "classify"):
            return self._intent_router.classify(message)  # type: ignore[union-attr]
        return RequestComplexity.MEDIUM

    def _select_model(self, complexity: RequestComplexity) -> str:
        """Select LLM model based on complexity — provider-agnostic cost optimization."""
        model_map = {
            RequestComplexity.SIMPLE: self._model_fast,
            RequestComplexity.MEDIUM: self._model_fast,
            RequestComplexity.COMPLEX: self._model_primary,
        }
        return model_map.get(complexity, self._model_fast)

    async def _handle_deterministic(self, user_id: UUID, message: str) -> str:
        """Handle requests that don't need LLM — direct API calls."""
        if hasattr(self._intent_router, "handle_deterministic"):
            return await self._intent_router.handle_deterministic(user_id, message)  # type: ignore[union-attr]

        # Smart mock response based on detected intent — uses real calendar store
        msg = message.lower()

        if any(w in msg for w in ("today", "schedule", "agenda")):
            return await self._list_events_response(
                user_id, days_offset=0, label="today"
            )

        if "tomorrow" in msg and not any(
            w in msg for w in ("create", "add", "schedule", "book")
        ):
            return await self._list_events_response(
                user_id, days_offset=1, label="tomorrow"
            )

        if any(w in msg for w in ("this week", "week")):
            return await self._list_events_response(
                user_id, days_offset=0, days_range=7, label="this week"
            )

        if any(w in msg for w in ("next meeting", "next event")):
            return await self._next_event_response(user_id)

        return "I can help with that. Let me check your calendar."

    async def _list_events_response(
        self,
        user_id: UUID,
        *,
        days_offset: int = 0,
        days_range: int = 1,
        label: str = "today",
    ) -> str:
        """Build a response listing real events from the calendar store."""
        if not self._calendar_provider:
            return f"📋 No calendar connected — cannot list events for {label}."

        now = datetime.now(tz=timezone.utc)
        start = (now + timedelta(days=days_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(days=days_range)
        events = await self._calendar_provider.list_events(user_id, start, end)

        if not events:
            return (
                f"📋 **Your schedule for {label}:**\n\nNo events — you're all clear! 🎉"
            )

        lines = [f"📋 **Your schedule for {label}:**\n"]
        for ev in events:
            t_start = ev.start_time.strftime("%H:%M")
            t_end = ev.end_time.strftime("%H:%M")
            loc = f" 📍 {ev.location}" if ev.location else ""
            lines.append(f"• {t_start} – {t_end}  **{ev.title}**{loc}")

        lines.append(
            f"\nYou have {len(events)} event{'s' if len(events) != 1 else ''} {label}."
        )
        lines.append("Would you like to add or change anything?")
        return "\n".join(lines)

    async def _next_event_response(self, user_id: UUID) -> str:
        """Return the next upcoming event."""
        if not self._calendar_provider:
            return "⏰ No calendar connected."

        now = datetime.now(tz=timezone.utc)
        end = now + timedelta(days=7)
        events = await self._calendar_provider.list_events(user_id, now, end)
        upcoming = [e for e in events if e.start_time >= now]
        if not upcoming:
            return "⏰ No upcoming events in the next 7 days. You're free!"

        ev = upcoming[0]
        loc = f"\n📍 {ev.location}" if ev.location else ""
        return (
            f"⏰ Your next event is **{ev.title}** at "
            f"{ev.start_time.strftime('%H:%M')} ({ev.duration_minutes} min).{loc}"
        )

    async def _execute_agent(
        self,
        user_id: UUID,
        conversation: Conversation,
        message: str,
        model: str,
    ) -> str:
        """Execute the LangGraph agent."""
        # Delegate to the injected agent executor
        if hasattr(self._agent_executor, "run"):
            return await self._agent_executor.run(  # type: ignore[union-attr]
                user_id=str(user_id),
                message=message,
                conversation=conversation,
                model=model,
            )

        # --- Smart mock responses with REAL event storage ---
        msg = message.lower()

        if any(w in msg for w in ("create", "add", "schedule", "book")):
            return await self._create_event_from_message(user_id, message)

        if any(w in msg for w in ("cancel", "delete", "remove")):
            return await self._delete_event_from_message(user_id, message)

        if any(w in msg for w in ("free", "available", "open", "slot")):
            return await self._free_slots_response(user_id)

        if any(w in msg for w in ("move", "reschedule", "change", "update")):
            return (
                "📝 **Event updated!**\n\n"
                "Moved **Team sync** from 3:00 PM → 4:00 PM.\n"
                "All attendees have been notified."
            )

        if any(w in msg for w in ("conflict", "overlap")):
            return (
                "⚠️ **Conflict detected:**\n\n"
                "• 14:00 – 15:00  Design review\n"
                "• 14:30 – 15:30  Client call\n\n"
                "These two events overlap by 30 minutes. "
                "Would you like me to move the client call to 15:00?"
            )

        if any(w in msg for w in ("hello", "hi", "hey", "help")):
            return (
                "👋 Hi! I'm your Calendar Agent. I can:\n\n"
                '• 📋 Show your schedule — *"What\'s on my calendar today?"*\n'
                '• ➕ Create events — *"Schedule a meeting tomorrow at 2pm"*\n'
                '• 🔍 Find free time — *"When am I free this week?"*\n'
                '• ❌ Cancel events — *"Cancel my 3pm meeting"*\n'
                '• 🔄 Reschedule — *"Move my standup to 10am"*\n\n'
                "What would you like to do?"
            )

        return (
            "I understand you want to manage your calendar. "
            "Could you be more specific? For example:\n\n"
            '• *"What\'s on my calendar today?"*\n'
            '• *"Schedule a meeting with John tomorrow at 2pm"*\n'
            '• *"When am I free this week?"*'
        )

    # ------------------------------------------------------------------
    # Helpers: real event creation / deletion / listing from chat
    # ------------------------------------------------------------------

    async def _create_event_from_message(self, user_id: UUID, message: str) -> str:
        """Parse a natural-language create request and store a real CalendarEvent."""
        title, start_dt, duration_min, location = self._parse_event_details(message)

        end_dt = start_dt + timedelta(minutes=duration_min)

        event = CalendarEvent(
            user_id=user_id,
            title=title,
            start_time=start_dt,
            end_time=end_dt,
            location=location,
        )

        if self._calendar_provider:
            event = await self._calendar_provider.create_event(user_id, event)
            # Invalidate calendar cache so the calendar view shows this event
            await self._cache.delete(f"events:{user_id}:*")

        loc_str = (
            f"\n📍 Location: {location}" if location else "\n📍 Location: Not specified"
        )
        return (
            "✅ **Event created!**\n\n"
            f"📅 **{title}**\n"
            f"🕐 {start_dt.strftime('%A, %b %d at %I:%M %p')} — "
            f"{end_dt.strftime('%I:%M %p')}\n"
            f"⏱ Duration: {duration_min} minutes{loc_str}\n\n"
            "I've added it to your calendar. "
            "Would you like to add attendees or set a reminder?"
        )

    def _parse_event_details(
        self, message: str
    ) -> tuple[str, datetime, int, str | None]:
        """
        Extract event title, start time, duration, and location from a message.
        Simple regex-based parser for common patterns.
        """
        msg = message.lower()
        now = datetime.now(tz=timezone.utc)

        # --- Determine day ---
        if "tomorrow" in msg:
            day = now + timedelta(days=1)
        elif "day after tomorrow" in msg:
            day = now + timedelta(days=2)
        elif "today" in msg or "tonight" in msg:
            day = now
        else:
            # Try "on Monday", "on Tuesday" etc.
            day_names = {
                "monday": 0,
                "tuesday": 1,
                "wednesday": 2,
                "thursday": 3,
                "friday": 4,
                "saturday": 5,
                "sunday": 6,
            }
            day = now + timedelta(days=1)  # default: tomorrow
            for name, weekday in day_names.items():
                if name in msg:
                    delta = (weekday - now.weekday()) % 7
                    if delta == 0:
                        delta = 7  # next week if same day
                    day = now + timedelta(days=delta)
                    break

        # --- Determine time ---
        hour, minute = 14, 0  # default 2 PM
        time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", msg)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            ampm = time_match.group(3)
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0

        start_dt = day.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # --- Duration ---
        duration = 30  # default
        dur_match = re.search(r"(\d+)\s*(?:min|minute|minutes|hr|hour|hours)", msg)
        if dur_match:
            val = int(dur_match.group(1))
            if "hour" in (dur_match.group(0) or ""):
                duration = val * 60
            else:
                duration = val

        # --- Title ---
        title = "Meeting"  # default
        # Try patterns like "schedule <title> tomorrow" or "create <title> at 2pm"
        title_patterns = [
            r"(?:schedule|create|add|book)\s+(?:a\s+)?(.+?)(?:\s+(?:at|on|for|from|tomorrow|today|next)\b)",
            r"(?:schedule|create|add|book)\s+(?:a\s+)?(.+?)$",
        ]
        for pat in title_patterns:
            m = re.search(pat, message, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip().rstrip(" .,!?")
                # Filter out time/day words
                skip = {
                    "at",
                    "on",
                    "for",
                    "from",
                    "tomorrow",
                    "today",
                    "pm",
                    "am",
                    "meeting",
                }
                if candidate.lower() not in skip and len(candidate) > 1:
                    title = candidate.title()
                    break

        # --- Location ---
        location: str | None = None
        loc_match = re.search(
            r"(?:at|in|@)\s+(\w[\w\s]*?)(?:\s+(?:at|on|from|tomorrow|today)\b|$)", msg
        )
        if loc_match and not re.match(r"\d", loc_match.group(1)):
            candidate = loc_match.group(1).strip()
            if len(candidate) > 2 and candidate.lower() not in ("a", "the", "my"):
                location = candidate.title()

        return title, start_dt, duration, location

    async def _delete_event_from_message(self, user_id: UUID, message: str) -> str:
        """Find and delete the most recently matching event."""
        if not self._calendar_provider:
            return "🗑️ No calendar connected — cannot delete events."

        now = datetime.now(tz=timezone.utc)
        events = await self._calendar_provider.list_events(
            user_id, now, now + timedelta(days=30)
        )

        if not events:
            return "🗑️ No upcoming events to delete."

        # Try to match by keyword in title
        msg = message.lower()
        target = None
        for ev in events:
            if ev.title.lower() in msg or any(
                w in ev.title.lower() for w in msg.split() if len(w) > 3
            ):
                target = ev
                break

        if not target:
            target = events[-1]  # delete the most recent if no match

        await self._calendar_provider.delete_event(user_id, str(target.id))
        # Invalidate calendar cache so the calendar view reflects deletion
        await self._cache.delete(f"events:{user_id}:*")

        return (
            "🗑️ **Event cancelled:**\n\n"
            f"~~{target.start_time.strftime('%I:%M %p')} — {target.title}~~\n\n"
            "The event has been removed from your calendar."
        )

    async def _free_slots_response(self, user_id: UUID) -> str:
        """Show real free slots from the calendar store."""
        if not self._calendar_provider:
            return "🔍 No calendar connected."

        now = datetime.now(tz=timezone.utc)
        start = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now.hour >= 9:
            start = now

        lines = ["🔍 **Available slots this week:**\n"]
        for offset in range(5):  # next 5 days
            day_start = (now + timedelta(days=offset)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )
            day_end = day_start.replace(hour=17, minute=0)
            if day_start < now:
                day_start = now

            events = await self._calendar_provider.list_events(
                user_id, day_start, day_end
            )
            label = day_start.strftime("%A, %b %d")

            if not events:
                lines.append(f"• **{label}:** 09:00 – 17:00 (all day free)")
                continue

            slots: list[str] = []
            cursor = day_start
            for ev in events:
                if (ev.start_time - cursor).total_seconds() >= 30 * 60:
                    slots.append(
                        f"{cursor.strftime('%H:%M')} – {ev.start_time.strftime('%H:%M')}"
                    )
                cursor = max(cursor, ev.end_time)
            if (day_end - cursor).total_seconds() >= 30 * 60:
                slots.append(
                    f"{cursor.strftime('%H:%M')} – {day_end.strftime('%H:%M')}"
                )

            if slots:
                lines.append(f"• **{label}:** {', '.join(slots)}")
            else:
                lines.append(f"• **{label}:** Fully booked")

        lines.append("\nWould you like me to book one of these?")
        return "\n".join(lines)
