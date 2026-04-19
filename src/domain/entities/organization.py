"""
Organization entity — represents a tenant (company/team) in the multi-tenant SaaS.
Pure domain object with no infrastructure dependencies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class OrgRole(str, Enum):
    """Roles within an organization."""

    OWNER = "owner"  # Super-admin: one per org, full control
    ADMIN = "admin"  # Can manage members, providers, org settings
    MEMBER = "member"  # Can create events, use email intelligence
    VIEWER = "viewer"  # Read-only access to org data


class ProviderType(str, Enum):
    """Supported email/calendar providers."""

    GOOGLE = "google"
    MICROSOFT = "microsoft"  # Outlook / Microsoft 365
    APPLE = "apple"


class ConnectionStatus(str, Enum):
    """Status of a provider connection."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    PENDING = "pending"


@dataclass
class Organization:
    """Core organization entity — the tenant boundary."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = ""
    slug: str = ""  # URL-friendly identifier
    owner_id: uuid.UUID | None = None
    domain: str | None = None  # e.g. "acme.com" for auto-join
    logo_url: str | None = None
    timezone: str = "UTC"
    is_active: bool = True
    max_members: int = 5  # plan-based limit
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OrgMembership:
    """Links a user to an organization with a specific role."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    role: OrgRole = OrgRole.MEMBER
    invited_by: uuid.UUID | None = None
    invited_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    joined_at: datetime | None = None
    is_active: bool = True


@dataclass
class ProviderConnection:
    """
    A user's connection to an email/calendar provider within an org.
    Stores OAuth tokens for Gmail, Outlook, etc.
    """

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    provider: ProviderType = ProviderType.GOOGLE
    provider_email: str = ""  # email on the provider side
    status: ConnectionStatus = ConnectionStatus.PENDING

    # OAuth tokens
    access_token: str = ""
    refresh_token: str | None = None
    token_expiry: datetime | None = None
    scopes: str = ""  # comma-separated scopes granted

    # Sync state
    calendar_sync_enabled: bool = True
    email_sync_enabled: bool = True
    last_sync_at: datetime | None = None
    webhook_channel_id: str | None = None  # for push notifications

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_token_valid(self) -> bool:
        """Check if the OAuth token is still valid."""
        if not self.access_token or not self.token_expiry:
            return False
        return self.token_expiry > datetime.now(timezone.utc)

    def refresh_tokens(
        self,
        access_token: str,
        refresh_token: str | None,
        expiry: datetime,
    ) -> None:
        """Update OAuth tokens after refresh."""
        self.access_token = access_token
        if refresh_token:
            self.refresh_token = refresh_token
        self.token_expiry = expiry
        self.status = ConnectionStatus.ACTIVE
        self.updated_at = datetime.now(timezone.utc)


@dataclass
class OrgLicenseConfig:
    """
    Per-organization license configuration.

    Defines how many seats (user licenses) are allowed and the cost per seat.
    OWNER/ADMIN can configure; the billing layer uses this to compute invoices.
    """

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)
    seat_cost_cents: int = 0  # Price per licensed seat in cents (0 = custom / free)
    max_seats: int = 5  # Maximum concurrent licensed users
    currency: str = "USD"  # ISO 4217 currency code
    billing_cycle: str = "monthly"  # "monthly" | "annual"
    notes: str = ""  # Admin-visible notes (e.g. contract ref)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def seat_cost_dollars(self) -> float:
        """Seat cost expressed in dollars (for display)."""
        return self.seat_cost_cents / 100

    def calculate_total_cost_cents(self, active_seats: int) -> int:
        """
        Return the total license cost in cents for *active_seats* users.
        Capped at max_seats if the org has grown beyond its license.
        """
        billed_seats = min(active_seats, self.max_seats)
        return billed_seats * self.seat_cost_cents
