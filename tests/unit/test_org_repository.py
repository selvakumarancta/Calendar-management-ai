"""
Unit tests for SQLAlchemy org repository implementations.

All SQLAlchemy calls are mocked — no DB needed.
Covers missing lines in:
  - SQLAlchemyOrganizationRepository  (47-48, 54-55, 75-76, 82-94, 97-100)
  - SQLAlchemyMembershipRepository    (161-162, 181, 192-197, 206-211, 222)
  - SQLAlchemyProviderConnectionRepo  (248-252, 255-260, 265-271, 276-285, 288-291, 294-314, 317-320, 324, 345)
  - SQLAlchemyLicenseConfigRepository (390-391, 401-426)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.organization import (
    ConnectionStatus,
    Organization,
    OrgLicenseConfig,
    OrgMembership,
    OrgRole,
    ProviderConnection,
    ProviderType,
)
from src.infrastructure.persistence.org_repository import (
    SQLAlchemyLicenseConfigRepository,
    SQLAlchemyMembershipRepository,
    SQLAlchemyOrganizationRepository,
    SQLAlchemyProviderConnectionRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _scalars_mock(items: list) -> MagicMock:
    sm = MagicMock()
    sm.__iter__ = MagicMock(return_value=iter(items))
    sm.all.return_value = items
    return sm


def _exec(
    scalar=None, scalars_list=None, rowcount: int = 0, scalar_one_val=0
) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalars.return_value = _scalars_mock(scalars_list or [])
    r.rowcount = rowcount
    r.scalar_one.return_value = scalar_one_val
    return r


def _sess(*results) -> AsyncMock:
    s = AsyncMock()
    s.execute = AsyncMock(side_effect=list(results))
    s.add = MagicMock()
    s.flush = AsyncMock()
    s.delete = AsyncMock()
    s.refresh = AsyncMock()
    return s


# ---------------------------------------------------------------------------
# Organization factoriess
# ---------------------------------------------------------------------------


def _org(**kw) -> Organization:
    return Organization(
        id=kw.pop("id", uuid.uuid4()),
        name=kw.pop("name", "Acme Corp"),
        slug=kw.pop("slug", "acme"),
        owner_id=kw.pop("owner_id", uuid.uuid4()),
        **kw,
    )


def _org_model(org: Organization | None = None) -> MagicMock:
    o = org or _org()
    m = MagicMock()
    m.id = o.id
    m.name = o.name
    m.slug = o.slug
    m.owner_id = o.owner_id
    m.domain = o.domain
    m.logo_url = o.logo_url
    m.timezone = o.timezone
    m.is_active = o.is_active
    m.max_members = o.max_members
    m.created_at = _NOW
    m.updated_at = _NOW
    return m


def _membership(**kw) -> OrgMembership:
    return OrgMembership(
        id=kw.pop("id", uuid.uuid4()),
        org_id=kw.pop("org_id", uuid.uuid4()),
        user_id=kw.pop("user_id", uuid.uuid4()),
        **kw,
    )


def _membership_model(m: OrgMembership | None = None) -> MagicMock:
    mem = m or _membership()
    mm = MagicMock()
    mm.id = mem.id
    mm.org_id = mem.org_id
    mm.user_id = mem.user_id
    mm.role = mem.role.value if isinstance(mem.role, OrgRole) else mem.role
    mm.invited_by = mem.invited_by
    mm.invited_at = mem.invited_at
    mm.joined_at = mem.joined_at
    mm.is_active = mem.is_active
    return mm


def _conn(**kw) -> ProviderConnection:
    return ProviderConnection(
        id=kw.pop("id", uuid.uuid4()),
        org_id=kw.pop("org_id", uuid.uuid4()),
        user_id=kw.pop("user_id", uuid.uuid4()),
        **kw,
    )


def _conn_model(c: ProviderConnection | None = None) -> MagicMock:
    con = c or _conn()
    m = MagicMock()
    m.id = con.id
    m.org_id = con.org_id
    m.user_id = con.user_id
    m.provider = (
        con.provider.value if isinstance(con.provider, ProviderType) else con.provider
    )
    m.provider_email = con.provider_email
    m.status = (
        con.status.value if isinstance(con.status, ConnectionStatus) else con.status
    )
    m.access_token = con.access_token
    m.refresh_token = con.refresh_token
    m.token_expiry = con.token_expiry
    m.scopes = con.scopes
    m.calendar_sync_enabled = con.calendar_sync_enabled
    m.email_sync_enabled = con.email_sync_enabled
    m.last_sync_at = con.last_sync_at
    m.webhook_channel_id = con.webhook_channel_id
    m.created_at = _NOW
    m.updated_at = _NOW
    return m


def _license_model(cfg: OrgLicenseConfig | None = None) -> MagicMock:
    lc = cfg or OrgLicenseConfig(org_id=uuid.uuid4())
    m = MagicMock()
    m.id = lc.id
    m.org_id = lc.org_id
    m.seat_cost_cents = lc.seat_cost_cents
    m.max_seats = lc.max_seats
    m.currency = lc.currency
    m.billing_cycle = lc.billing_cycle
    m.notes = lc.notes
    m.created_at = _NOW
    m.updated_at = _NOW
    return m


# ===========================================================================
# SQLAlchemyOrganizationRepository
# ===========================================================================


class TestOrgRepository:

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_by_id_returns_entity_when_found(self):
        """Lines 47-48: found path returns Organization entity."""
        o = _org()
        sess = _sess(_exec(scalar=_org_model(o)))
        repo = SQLAlchemyOrganizationRepository(sess)
        result = await repo.get_by_id(o.id)
        assert result is not None
        assert result.id == o.id
        assert result.name == "Acme Corp"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_by_id_returns_none_when_not_found(self):
        """get_by_id None path."""
        sess = _sess(_exec(scalar=None))
        repo = SQLAlchemyOrganizationRepository(sess)
        result = await repo.get_by_id(uuid.uuid4())
        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_by_slug_returns_entity_when_found(self):
        """Lines 54-55: found path returns entity."""
        o = _org()
        sess = _sess(_exec(scalar=_org_model(o)))
        repo = SQLAlchemyOrganizationRepository(sess)
        result = await repo.get_by_slug("acme")
        assert result is not None
        assert result.slug == "acme"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_by_user_returns_entities(self):
        """Lines 57-69: list orgs for a user."""
        o = _org()
        sess = _sess(_exec(scalars_list=[_org_model(o)]))
        repo = SQLAlchemyOrganizationRepository(sess)
        result = await repo.list_by_user(uuid.uuid4())
        assert len(result) == 1
        assert result[0].name == "Acme Corp"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_calls_add_flush_and_returns_entity(self):
        """Lines 71-76: create adds model, flushes, refreshes, returns entity."""
        o = _org()
        model = _org_model(o)
        sess = AsyncMock()
        sess.add = MagicMock()
        sess.flush = AsyncMock()
        sess.refresh = AsyncMock()

        with (
            __import__("unittest.mock", fromlist=["patch"]).patch.object(
                SQLAlchemyOrganizationRepository, "_to_model", return_value=model
            ),
            __import__("unittest.mock", fromlist=["patch"]).patch.object(
                SQLAlchemyOrganizationRepository, "_to_entity", return_value=o
            ),
        ):
            repo = SQLAlchemyOrganizationRepository(sess)
            result = await repo.create(o)

        assert result is o
        sess.flush.assert_called_once()
        sess.refresh.assert_called_once_with(model)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_found_model_updates_fields(self):
        """Lines 82-94: update patches all fields and returns entity."""
        o = _org(name="Updated Name", slug="updated")
        model = _org_model(o)
        sess = _sess(_exec(scalar=model))

        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
            SQLAlchemyOrganizationRepository, "_to_entity", return_value=o
        ):
            repo = SQLAlchemyOrganizationRepository(sess)
            result = await repo.update(o)

        assert result is o
        assert model.name == "Updated Name"
        sess.flush.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_not_found_returns_org(self):
        """Line 94: update not found returns input org."""
        o = _org()
        sess = _sess(_exec(scalar=None))
        repo = SQLAlchemyOrganizationRepository(sess)
        result = await repo.update(o)
        assert result is o

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_delete_returns_true_when_rows_deleted(self):
        """Lines 97-100: delete returns True when rowcount > 0."""
        sess = _sess(_exec(rowcount=1))
        repo = SQLAlchemyOrganizationRepository(sess)
        result = await repo.delete(uuid.uuid4())
        assert result is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_delete_returns_false_when_no_rows(self):
        """Lines 97-100: delete returns False when rowcount == 0."""
        sess = _sess(_exec(rowcount=0))
        repo = SQLAlchemyOrganizationRepository(sess)
        result = await repo.delete(uuid.uuid4())
        assert result is False


# ===========================================================================
# SQLAlchemyMembershipRepository
# ===========================================================================


class TestMembershipRepository:

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_members_returns_list(self):
        """get_members returns list of active members."""
        mem = _membership()
        sess = _sess(_exec(scalars_list=[_membership_model(mem)]))
        repo = SQLAlchemyMembershipRepository(sess)
        result = await repo.get_members(mem.org_id)
        assert len(result) == 1
        assert result[0].id == mem.id

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_membership_returns_entity_when_found(self):
        """Lines 161-162: get_membership found path."""
        mem = _membership()
        sess = _sess(_exec(scalar=_membership_model(mem)))
        repo = SQLAlchemyMembershipRepository(sess)
        result = await repo.get_membership(mem.org_id, mem.user_id)
        assert result is not None
        assert result.user_id == mem.user_id

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_membership_returns_none_when_not_found(self):
        """get_membership None path."""
        sess = _sess(_exec(scalar=None))
        repo = SQLAlchemyMembershipRepository(sess)
        result = await repo.get_membership(uuid.uuid4(), uuid.uuid4())
        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_add_member_adds_and_flushes(self):
        """Line 181: add_member adds model and flushes."""
        mem = _membership()
        sess = AsyncMock()
        sess.add = MagicMock()
        sess.flush = AsyncMock()
        repo = SQLAlchemyMembershipRepository(sess)
        result = await repo.add_member(mem)
        assert result is mem
        sess.add.assert_called_once()
        sess.flush.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_role_found_updates(self):
        """Lines 192-197: update_role when model found."""
        mem = _membership()
        model = _membership_model(mem)
        sess = _sess(_exec(scalar=model))

        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
            SQLAlchemyMembershipRepository, "_to_entity", return_value=mem
        ):
            repo = SQLAlchemyMembershipRepository(sess)
            result = await repo.update_role(mem.org_id, mem.user_id, "admin")

        assert model.role == "admin"
        sess.flush.assert_called_once()
        assert result is mem

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_role_not_found_returns_none(self):
        """update_role None path."""
        sess = _sess(_exec(scalar=None))
        repo = SQLAlchemyMembershipRepository(sess)
        result = await repo.update_role(uuid.uuid4(), uuid.uuid4(), "admin")
        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_remove_member_found_deactivates(self):
        """Lines 206-211: remove_member sets is_active=False."""
        mem = _membership()
        model = _membership_model(mem)
        sess = _sess(_exec(scalar=model))
        repo = SQLAlchemyMembershipRepository(sess)
        result = await repo.remove_member(mem.org_id, mem.user_id)
        assert result is True
        assert model.is_active is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_remove_member_not_found_returns_false(self):
        """remove_member False path."""
        sess = _sess(_exec(scalar=None))
        repo = SQLAlchemyMembershipRepository(sess)
        result = await repo.remove_member(uuid.uuid4(), uuid.uuid4())
        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_count_members_returns_integer(self):
        """Line 222: count_members returns scalar_one() result."""
        sess = _sess(_exec(scalar_one_val=7))
        repo = SQLAlchemyMembershipRepository(sess)
        result = await repo.count_members(uuid.uuid4())
        assert result == 7


# ===========================================================================
# SQLAlchemyProviderConnectionRepository
# ===========================================================================


class TestProviderConnectionRepository:

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_by_id_returns_entity(self):
        """Lines 248-252: get_by_id found."""
        c = _conn()
        sess = _sess(_exec(scalar=_conn_model(c)))
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.get_by_id(c.id)
        assert result is not None
        assert result.id == c.id

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_by_id_returns_none(self):
        """get_by_id None path."""
        sess = _sess(_exec(scalar=None))
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.get_by_id(uuid.uuid4())
        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_by_org_returns_list(self):
        """Lines 255-260: list_by_org."""
        c = _conn()
        sess = _sess(_exec(scalars_list=[_conn_model(c)]))
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.list_by_org(c.org_id)
        assert len(result) == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_by_user_without_org_filter(self):
        """Lines 262-271: list_by_user without org_id."""
        c = _conn()
        sess = _sess(_exec(scalars_list=[_conn_model(c)]))
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.list_by_user(c.user_id)
        assert len(result) == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_list_by_user_with_org_filter(self):
        """Lines 262-271: list_by_user with org_id → where clause added."""
        c = _conn()
        sess = _sess(_exec(scalars_list=[_conn_model(c)]))
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.list_by_user(c.user_id, org_id=c.org_id)
        assert len(result) == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_active_connection_returns_entity(self):
        """Lines 276-285: get_active_connection found."""
        c = _conn(status=ConnectionStatus.ACTIVE)
        sess = _sess(_exec(scalar=_conn_model(c)))
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.get_active_connection(c.user_id, "google", c.org_id)
        assert result is not None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_adds_and_flushes(self):
        """Lines 288-291: create."""
        c = _conn()
        sess = AsyncMock()
        sess.add = MagicMock()
        sess.flush = AsyncMock()
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.create(c)
        assert result is c
        sess.flush.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_found_updates_all_token_fields(self):
        """Lines 294-314: update found model patches fields."""
        c = _conn(
            access_token="new_token",
            status=ConnectionStatus.ACTIVE,
        )
        model = _conn_model(c)
        sess = _sess(_exec(scalar=model))
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.update(c)
        assert result is c
        assert model.access_token == "new_token"
        sess.flush.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_not_found_returns_conn(self):
        """update not-found path — still returns conn."""
        c = _conn()
        sess = _sess(_exec(scalar=None))
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.update(c)
        assert result is c

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_delete_returns_true_when_row_deleted(self):
        """Lines 317-320: delete → rowcount > 0."""
        sess = _sess(_exec(rowcount=1))
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.delete(uuid.uuid4())
        assert result is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_delete_returns_false_when_no_row(self):
        """delete rowcount == 0 → False."""
        sess = _sess(_exec(rowcount=0))
        repo = SQLAlchemyProviderConnectionRepository(sess)
        result = await repo.delete(uuid.uuid4())
        assert result is False


# ===========================================================================
# SQLAlchemyLicenseConfigRepository
# ===========================================================================


class TestLicenseConfigRepository:

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_by_org_returns_entity_when_found(self):
        """Lines 390-391: get_by_org found."""
        cfg = OrgLicenseConfig(org_id=uuid.uuid4(), seat_cost_cents=500, max_seats=10)
        lm = _license_model(cfg)
        sess = _sess(_exec(scalar=lm))
        repo = SQLAlchemyLicenseConfigRepository(sess)
        result = await repo.get_by_org(cfg.org_id)
        assert result is not None
        assert result.seat_cost_cents == 500

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_by_org_returns_none_when_not_found(self):
        """get_by_org None path."""
        sess = _sess(_exec(scalar=None))
        repo = SQLAlchemyLicenseConfigRepository(sess)
        result = await repo.get_by_org(uuid.uuid4())
        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upsert_updates_existing_model(self):
        """Lines 401-411: upsert when model already exists → updates fields."""
        cfg = OrgLicenseConfig(org_id=uuid.uuid4(), seat_cost_cents=1000, max_seats=20)
        lm = _license_model(cfg)
        sess = _sess(_exec(scalar=lm))

        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
            SQLAlchemyLicenseConfigRepository, "_to_entity", return_value=cfg
        ):
            repo = SQLAlchemyLicenseConfigRepository(sess)
            result = await repo.upsert(cfg)

        assert result is cfg
        assert lm.seat_cost_cents == 1000
        assert lm.max_seats == 20
        sess.flush.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upsert_inserts_new_model_when_not_found(self):
        """Lines 413-426: upsert when no existing model → inserts new row."""
        cfg = OrgLicenseConfig(org_id=uuid.uuid4(), seat_cost_cents=250, max_seats=5)
        sess = _sess(_exec(scalar=None))
        sess = AsyncMock()
        sess.execute = AsyncMock(return_value=_exec(scalar=None))
        sess.add = MagicMock()
        sess.flush = AsyncMock()
        repo = SQLAlchemyLicenseConfigRepository(sess)
        result = await repo.upsert(cfg)
        assert result is cfg
        sess.add.assert_called_once()
        sess.flush.assert_called_once()
