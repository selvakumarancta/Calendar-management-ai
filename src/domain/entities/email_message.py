"""
Email Intelligence entities — represents emails and their analysis results.
Pure domain objects, provider-agnostic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class EmailCategory(str, Enum):
    """Category of actionable email detected by analysis."""

    MEETING_REQUEST = "meeting_request"
    MEETING_RESCHEDULE = "meeting_reschedule"
    MEETING_CANCELLATION = "meeting_cancellation"
    TASK_ASSIGNMENT = "task_assignment"
    DEADLINE_REMINDER = "deadline_reminder"
    APPOINTMENT = "appointment"
    EVENT_INVITATION = "event_invitation"
    FOLLOW_UP = "follow_up"
    NON_ACTIONABLE = "non_actionable"


class SuggestionStatus(str, Enum):
    """Status of a schedule suggestion."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_SCHEDULED = "auto_scheduled"
    EXPIRED = "expired"


class SuggestionPriority(str, Enum):
    """Priority level for a suggestion."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DraftStatus(str, Enum):
    """Status of a generated email draft reply."""

    PENDING = "pending"       # Draft created in Gmail, awaiting user review
    SENT = "sent"             # User sent the draft
    DISCARDED = "discarded"   # User deleted without sending
    AUTOPILOT_SENT = "autopilot_sent"  # Sent automatically in autopilot mode


@dataclass
class ThreadMessage:
    """A single message within an email thread (for context)."""

    sender: str = ""
    recipient: str = ""
    date: str = ""
    body: str = ""
    is_from_user: bool = False


@dataclass
class EmailMessage:
    """Represents a parsed email from any provider (Gmail, Outlook)."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    provider_message_id: str = ""  # Gmail/Outlook message ID
    provider: str = ""  # "google" | "microsoft"
    user_id: uuid.UUID | None = None

    subject: str = ""
    sender_email: str = ""
    sender_name: str = ""
    recipients: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    body_text: str = ""
    body_snippet: str = ""  # First ~200 chars for quick display

    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    thread_id: str = ""  # Conversation thread ID
    thread_messages: list[ThreadMessage] = field(default_factory=list)  # Full thread history
    labels: list[str] = field(default_factory=list)
    has_attachments: bool = False
    is_read: bool = False

    @property
    def body_preview(self) -> str:
        """Return snippet or truncated body for display."""
        if self.body_snippet:
            return self.body_snippet
        return (
            self.body_text[:200] + "..."
            if len(self.body_text) > 200
            else self.body_text
        )


@dataclass
class ClassificationResult:
    """Structured LLM classification of an email's scheduling intent."""

    needs_draft: bool = False               # True if a draft reply is needed
    confidence: float = 0.0
    category: EmailCategory = EmailCategory.NON_ACTIONABLE
    summary: str = ""
    proposed_times: list[str] = field(default_factory=list)  # Times extracted from email
    participants: list[str] = field(default_factory=list)
    duration_minutes: int | None = None
    is_sales_email: bool = False            # Unsolicited cold outreach — skip
    already_resolved: bool = False          # Thread already has a confirmed time


@dataclass
class EmailAnalysis:
    """LLM analysis result for a single email."""

    email_id: uuid.UUID = field(default_factory=uuid.uuid4)
    category: EmailCategory = EmailCategory.NON_ACTIONABLE
    confidence: float = 0.0  # 0.0 to 1.0

    # Extracted scheduling details
    suggested_title: str = ""
    suggested_date: str = ""  # ISO date or "next Tuesday", etc.
    suggested_time: str = ""  # "2:00 PM", "14:00", etc.
    suggested_duration_minutes: int = 30
    suggested_location: str = ""
    suggested_attendees: list[str] = field(default_factory=list)

    # Context
    summary: str = ""  # One-line summary of the email
    action_required: str = ""  # What action is needed
    urgency: SuggestionPriority = SuggestionPriority.MEDIUM

    is_actionable: bool = False
    is_sales_email: bool = False
    already_resolved: bool = False


@dataclass
class DraftReply:
    """An AI-generated draft email reply stored in the user's Gmail drafts folder."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID | None = None
    org_id: uuid.UUID | None = None

    # Source email / thread
    email_provider_id: str = ""          # Original email's provider msg ID
    thread_id: str = ""                  # Gmail thread ID
    email_subject: str = ""
    email_sender: str = ""
    email_received_at: datetime | None = None

    # The draft
    draft_provider_id: str = ""          # Gmail draft ID (for deletion/update)
    reply_to: str = ""                   # Who the reply goes to
    reply_cc: str = ""
    reply_subject: str = ""
    reply_body: str = ""                 # Full draft body text

    # Proposed calendar slots extracted from the draft body
    proposed_windows: list[dict] = field(default_factory=list)  # [{date, start, end}]
    duration_minutes: int = 30
    event_summary: str = ""

    # Invite proposal (set when draft confirms a meeting)
    pending_invite: dict | None = None   # {title, start, end, attendees, location}

    # Status
    status: DraftStatus = DraftStatus.PENDING
    is_group_meeting: bool = False       # Group meetings always go through draft review
    autopilot_eligible: bool = False     # 1:1 meeting that could be autopilot-sent

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: datetime | None = None


@dataclass
class ScheduleSuggestion:
    """A concrete schedule suggestion derived from email analysis."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID | None = None
    org_id: uuid.UUID | None = None

    # Source email
    email_provider_id: str = ""
    email_subject: str = ""
    email_sender: str = ""
    email_received_at: datetime | None = None
    email_snippet: str = ""

    # Analysis
    category: EmailCategory = EmailCategory.MEETING_REQUEST
    confidence: float = 0.0
    priority: SuggestionPriority = SuggestionPriority.MEDIUM

    # Proposed event
    title: str = ""
    description: str = ""
    proposed_start: datetime | None = None
    proposed_end: datetime | None = None
    location: str = ""
    attendees: list[str] = field(default_factory=list)

    # Status tracking
    status: SuggestionStatus = SuggestionStatus.PENDING
    calendar_event_id: str | None = None  # Set when approved/scheduled

    # Draft reply link (if a draft was created)
    draft_reply_id: uuid.UUID | None = None

    # Conflict info
    has_conflict: bool = False
    conflict_details: str = ""
    alternative_slots: list[dict] = field(default_factory=list)  # [{start, end}]

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


@dataclass
class EmailScanResult:
    """Result of scanning a user's inbox."""

    user_id: uuid.UUID
    provider: str
    scanned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    emails_scanned: int = 0
    actionable_found: int = 0
    suggestions_created: int = 0
    drafts_created: int = 0
    sales_emails_filtered: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class SchedulingLink:
    """A unique shareable link that lets someone schedule with the user."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID | None = None
    draft_reply_id: uuid.UUID | None = None

    # What's being scheduled
    event_summary: str = ""
    duration_minutes: int = 30
    attendee_email: str = ""
    thread_id: str = ""
    subject: str = ""
    mode: str = "availability"  # "availability" | "suggested" | "confirmation"

    # Proposed windows (for "suggested" mode)
    proposed_windows: list[dict] = field(default_factory=list)
    user_timezone: str = "UTC"

    # Status
    is_active: bool = True
    booked_at: datetime | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None


@dataclass
class UserGuide:
    """An AI-generated user preference guide (scheduling or email style)."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID | None = None
    guide_type: str = ""  # "scheduling_preferences" | "email_style"
    content: str = ""     # Markdown text of the guide
    version: int = 1
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    emails_analyzed: int = 0


@dataclass
class AnalyticsEvent:
    """A single analytics event for tracking agent actions."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID | None = None
    event_type: str = ""   # "draft_composed" | "draft_sent" | "draft_discarded" | "invite_created" | "scan_completed"
    properties: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))



class EmailCategory(str, Enum):
    """Category of actionable email detected by analysis."""

    MEETING_REQUEST = "meeting_request"
    MEETING_RESCHEDULE = "meeting_reschedule"
    MEETING_CANCELLATION = "meeting_cancellation"
    TASK_ASSIGNMENT = "task_assignment"
    DEADLINE_REMINDER = "deadline_reminder"
    APPOINTMENT = "appointment"
    EVENT_INVITATION = "event_invitation"
    FOLLOW_UP = "follow_up"
    NON_ACTIONABLE = "non_actionable"


class SuggestionStatus(str, Enum):
    """Status of a schedule suggestion."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_SCHEDULED = "auto_scheduled"
    EXPIRED = "expired"


class SuggestionPriority(str, Enum):
    """Priority level for a suggestion."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class EmailMessage:
    """Represents a parsed email from any provider (Gmail, Outlook)."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    provider_message_id: str = ""  # Gmail/Outlook message ID
    provider: str = ""  # "google" | "microsoft"
    user_id: uuid.UUID | None = None

    subject: str = ""
    sender_email: str = ""
    sender_name: str = ""
    recipients: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    body_text: str = ""
    body_snippet: str = ""  # First ~200 chars for quick display

    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    thread_id: str = ""  # Conversation thread ID
    labels: list[str] = field(default_factory=list)
    has_attachments: bool = False
    is_read: bool = False

    @property
    def body_preview(self) -> str:
        """Return snippet or truncated body for display."""
        if self.body_snippet:
            return self.body_snippet
        return (
            self.body_text[:200] + "..."
            if len(self.body_text) > 200
            else self.body_text
        )


@dataclass
class EmailAnalysis:
    """LLM analysis result for a single email."""

    email_id: uuid.UUID = field(default_factory=uuid.uuid4)
    category: EmailCategory = EmailCategory.NON_ACTIONABLE
    confidence: float = 0.0  # 0.0 to 1.0

    # Extracted scheduling details
    suggested_title: str = ""
    suggested_date: str = ""  # ISO date or "next Tuesday", etc.
    suggested_time: str = ""  # "2:00 PM", "14:00", etc.
    suggested_duration_minutes: int = 30
    suggested_location: str = ""
    suggested_attendees: list[str] = field(default_factory=list)

    # Context
    summary: str = ""  # One-line summary of the email
    action_required: str = ""  # What action is needed
    urgency: SuggestionPriority = SuggestionPriority.MEDIUM

    is_actionable: bool = False


@dataclass
class ScheduleSuggestion:
    """A concrete schedule suggestion derived from email analysis."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID | None = None
    org_id: uuid.UUID | None = None

    # Source email
    email_provider_id: str = ""
    email_subject: str = ""
    email_sender: str = ""
    email_received_at: datetime | None = None
    email_snippet: str = ""

    # Analysis
    category: EmailCategory = EmailCategory.MEETING_REQUEST
    confidence: float = 0.0
    priority: SuggestionPriority = SuggestionPriority.MEDIUM

    # Proposed event
    title: str = ""
    description: str = ""
    proposed_start: datetime | None = None
    proposed_end: datetime | None = None
    location: str = ""
    attendees: list[str] = field(default_factory=list)

    # Status tracking
    status: SuggestionStatus = SuggestionStatus.PENDING
    calendar_event_id: str | None = None  # Set when approved/scheduled

    # Conflict info
    has_conflict: bool = False
    conflict_details: str = ""
    alternative_slots: list[dict] = field(default_factory=list)  # [{start, end}]

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


@dataclass
class EmailScanResult:
    """Result of scanning a user's inbox."""

    user_id: uuid.UUID
    provider: str
    scanned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    emails_scanned: int = 0
    actionable_found: int = 0
    suggestions_created: int = 0
    errors: list[str] = field(default_factory=list)
