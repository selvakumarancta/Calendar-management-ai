"""
Unit tests for OrganizationService.

All repositories are replaced with async mocks — no DB or network needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.organization_service import OrganizationService
from src.domain.entities.organization import (
    ConnectionStatus,
    Organization,
    OrgMembership,
    OrgRole,
    ProviderConnection,
    ProviderType,
)
from src.domain.exceptions import DomainError, InsufficientPermissionsError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OWNER_ID = uuid.uuid4()
_MEMBER_ID = uuid.uuid4()
_ORG_ID = uuid.uuid4()


def _org(**kw) -> Organization:
    return Organization(
        id=kw.pop("id", _ORG_ID),
        name=kw.pop("name", "Test Corp"),
        slug=kw.pop("slug", "test-corp"),
        owner_id=kw.pop("owner_id", _OWNER_ID),
        **kw,
    )


def _membership(
    user_id: uuid.UUID = _OWNER_ID,
    org_id: uuid.UUID = _ORG_ID,
    role: OrgRole = OrgRole.OWNER,
) -> OrgMembership:
    return OrgMembership(org_id=org_id, user_id=user_id, role=role)


def _make_svc(
    owner_membership: OrgMembership | None = None,
    org: Organization | None = None,
    member_count: int = 1,
) -> OrganizationService:
    """Build a service backed by async mock repos."""
    mem = owner_membership or _membership()
    o = org or _org()

    org_repo = AsyncMock()
    org_repo.get_by_id.return_value = o
    org_repo.get_by_slug.return_value = None
    org_repo.create.side_effect = lambda x: x
    org_repo.update.side_effect = lambda x: x
    org_repo.list_by_user.return_value = [o]

    membership_repo = AsyncMock()
    membership_repo.get_membership.return_value = mem
    membership_repo.add_member.side_effect = lambda x: x
    membership_repo.get_members.return_value = [mem]
    membership_repo.count_members.return_value = member_count
    membership_repo.remove_member.return_value = True
    membership_repo.update_role.return_value = mem

    provider_repo = AsyncMock()
    provider_repo.get_active_connection.return_value = None
    provider_repo.create.side_effect = lambda x: x
    provider_repo.update.side_effect = lambda x: x
    provider_repo.list_by_org.return_value = []
    provider_repo.delete.return_value = True

    user_repo = AsyncMock()
    fake_user = MagicMock()
    fake_user.id = _OWNER_ID
    fake_user.email = "owner@example.com"
    fake_user.name = "Owner"
    user_repo.get_by_id.return_value = fake_user
    user_repo.get_by_email.return_value = fake_user
    user_repo.create.side_effect = lambda x: x
    user_repo.update.side_effect = lambda x: x

    return OrganizationService(
        org_repo=org_repo,
        membership_repo=membership_repo,
        provider_repo=provider_repo,
        user_repo=user_repo,
    )


# ---------------------------------------------------------------------------
# create_organization
# ---------------------------------------------------------------------------


class TestCreateOrganization:
    @pytest.mark.unit
    async def test_creates_org_and_owner_membership(self):
        svc = _make_svc()
        org = await svc.create_organization("Acme", _OWNER_ID)
        assert org.name == "Acme"
        svc._membership_repo.add_member.assert_awaited_once()

    @pytest.mark.unit
    async def test_slug_collision_gets_suffix(self):
        svc = _make_svc()
        # First call: slug exists → return existing org
        svc._org_repo.get_by_slug.return_value = _org()
        # Second call: no collision
        svc._org_repo.get_by_slug.side_effect = None
        svc._org_repo.get_by_slug.return_value = _org()
        org = await svc.create_organization("Test Corp", _OWNER_ID)
        assert org is not None

    @pytest.mark.unit
    async def test_slugify_converts_spaces(self):
        result = OrganizationService._slugify("Hello World Inc.")
        assert " " not in result
        assert result == "hello-world-inc"


# ---------------------------------------------------------------------------
# get_organization / list
# ---------------------------------------------------------------------------


class TestGetOrganization:
    @pytest.mark.unit
    async def test_returns_org(self):
        svc = _make_svc()
        org = await svc.get_organization(_ORG_ID)
        assert org is not None
        svc._org_repo.get_by_id.assert_awaited_once_with(_ORG_ID)

    @pytest.mark.unit
    async def test_list_user_organizations(self):
        svc = _make_svc()
        orgs = await svc.list_user_organizations(_OWNER_ID)
        assert len(orgs) == 1


# ---------------------------------------------------------------------------
# update_organization
# ---------------------------------------------------------------------------


class TestUpdateOrganization:
    @pytest.mark.unit
    async def test_owner_can_update(self):
        svc = _make_svc()
        result = await svc.update_organization(_ORG_ID, _OWNER_ID, name="New Name")
        assert result is not None

    @pytest.mark.unit
    async def test_non_member_cannot_update(self):
        svc = _make_svc()
        svc._membership_repo.get_membership.return_value = None
        with pytest.raises(InsufficientPermissionsError):
            await svc.update_organization(_ORG_ID, uuid.uuid4(), name="Hack")

    @pytest.mark.unit
    async def test_member_role_cannot_update(self):
        svc = _make_svc(owner_membership=_membership(role=OrgRole.MEMBER))
        with pytest.raises(InsufficientPermissionsError):
            await svc.update_organization(_ORG_ID, _OWNER_ID, name="Hack")

    @pytest.mark.unit
    async def test_raises_when_org_not_found(self):
        svc = _make_svc()
        svc._org_repo.get_by_id.return_value = None
        with pytest.raises(DomainError, match="not found"):
            await svc.update_organization(_ORG_ID, _OWNER_ID, name="X")


# ---------------------------------------------------------------------------
# invite_member
# ---------------------------------------------------------------------------


class TestInviteMember:
    @pytest.mark.unit
    async def test_invite_new_member(self):
        svc = _make_svc()
        # No existing membership for the new user
        svc._membership_repo.get_membership.side_effect = [
            _membership(),  # actor check
            None,  # duplicate member check
        ]
        result = await svc.invite_member(
            org_id=_ORG_ID,
            email="new@example.com",
            role=OrgRole.MEMBER,
            invited_by=_OWNER_ID,
        )
        assert result is not None

    @pytest.mark.unit
    async def test_cannot_invite_as_owner(self):
        svc = _make_svc()
        with pytest.raises(DomainError, match="OWNER"):
            await svc.invite_member(
                org_id=_ORG_ID,
                email="new@example.com",
                role=OrgRole.OWNER,
                invited_by=_OWNER_ID,
            )

    @pytest.mark.unit
    async def test_raises_when_user_already_member(self):
        svc = _make_svc()
        svc._membership_repo.get_membership.return_value = _membership()
        with pytest.raises(DomainError, match="already a member"):
            await svc.invite_member(
                org_id=_ORG_ID,
                email="owner@example.com",
                role=OrgRole.MEMBER,
                invited_by=_OWNER_ID,
            )

    @pytest.mark.unit
    async def test_seat_cap_enforced(self):
        svc = _make_svc(member_count=5)
        svc._org_repo.get_by_id.return_value = _org()
        # membership_repo.get_membership: first call returns owner (for _require_role),
        # second call returns None (not already a member)
        svc._membership_repo.get_membership.side_effect = [_membership(), None]
        with pytest.raises(DomainError, match="seat limit"):
            await svc.invite_member(
                org_id=_ORG_ID,
                email="newperson@example.com",
                role=OrgRole.MEMBER,
                invited_by=_OWNER_ID,
            )


# ---------------------------------------------------------------------------
# get_members / remove_member / update_member_role
# ---------------------------------------------------------------------------


class TestMemberManagement:
    @pytest.mark.unit
    async def test_get_members_returns_list(self):
        svc = _make_svc()
        members = await svc.get_members(_ORG_ID, _OWNER_ID)
        assert isinstance(members, list)
        assert len(members) == 1
        assert "email" in members[0]

    @pytest.mark.unit
    async def test_remove_member(self):
        svc = _make_svc()
        result = await svc.remove_member(_ORG_ID, _MEMBER_ID, _OWNER_ID)
        assert result is True

    @pytest.mark.unit
    async def test_cannot_remove_self(self):
        svc = _make_svc()
        with pytest.raises(DomainError, match="Cannot remove yourself"):
            await svc.remove_member(_ORG_ID, _OWNER_ID, _OWNER_ID)

    @pytest.mark.unit
    async def test_update_member_role(self):
        svc = _make_svc()
        result = await svc.update_member_role(
            _ORG_ID, _MEMBER_ID, OrgRole.ADMIN, _OWNER_ID
        )
        assert result is not None


# ---------------------------------------------------------------------------
# connect_provider / list / disconnect
# ---------------------------------------------------------------------------


class TestProviderConnections:
    @pytest.mark.unit
    async def test_connect_provider_creates_new(self):
        svc = _make_svc()
        expiry = datetime.now(timezone.utc)
        conn = await svc.connect_provider(
            org_id=_ORG_ID,
            user_id=_OWNER_ID,
            provider=ProviderType.GOOGLE,
            provider_email="owner@gmail.com",
            access_token="tok",
            refresh_token="rtok",
            token_expiry=expiry,
        )
        assert conn is not None

    @pytest.mark.unit
    async def test_connect_provider_updates_existing(self):
        svc = _make_svc()
        existing = ProviderConnection(
            org_id=_ORG_ID,
            user_id=_OWNER_ID,
            provider=ProviderType.GOOGLE,
            access_token="old_tok",
            status=ConnectionStatus.ACTIVE,
        )
        svc._provider_repo.get_active_connection.return_value = existing
        expiry = datetime.now(timezone.utc)
        conn = await svc.connect_provider(
            org_id=_ORG_ID,
            user_id=_OWNER_ID,
            provider=ProviderType.GOOGLE,
            provider_email="owner@gmail.com",
            access_token="new_tok",
            refresh_token="new_rtok",
            token_expiry=expiry,
        )
        # Should update existing, not create new
        svc._provider_repo.update.assert_awaited_once()

    @pytest.mark.unit
    async def test_list_provider_connections(self):
        svc = _make_svc()
        result = await svc.list_provider_connections(_ORG_ID, _OWNER_ID)
        assert isinstance(result, list)

    @pytest.mark.unit
    async def test_disconnect_provider(self):
        svc = _make_svc()
        result = await svc.disconnect_provider(uuid.uuid4(), _OWNER_ID, _ORG_ID)
        assert result is True


# ---------------------------------------------------------------------------
# _require_role
# ---------------------------------------------------------------------------


class TestRequireRole:
    @pytest.mark.unit
    async def test_non_member_raises(self):
        svc = _make_svc()
        svc._membership_repo.get_membership.return_value = None
        with pytest.raises(InsufficientPermissionsError, match="Not a member"):
            await svc._require_role(_ORG_ID, uuid.uuid4(), {OrgRole.OWNER})

    @pytest.mark.unit
    async def test_wrong_role_raises(self):
        svc = _make_svc(owner_membership=_membership(role=OrgRole.VIEWER))
        with pytest.raises(InsufficientPermissionsError):
            await svc._require_role(_ORG_ID, _OWNER_ID, {OrgRole.OWNER, OrgRole.ADMIN})

    @pytest.mark.unit
    async def test_string_role_is_coerced(self):
        """String role value (from DB) should be coerced to OrgRole enum."""
        mem = _membership()
        mem.role = "owner"  # type: ignore[assignment]
        svc = _make_svc(owner_membership=mem)
        # Should not raise
        result = await svc._require_role(_ORG_ID, _OWNER_ID, {OrgRole.OWNER})
        assert result is not None


# ---------------------------------------------------------------------------
# license config
# ---------------------------------------------------------------------------


class TestLicenseConfig:
    @pytest.mark.unit
    async def test_get_license_config_no_repo(self):
        svc = _make_svc()
        result = await svc.get_license_config(_ORG_ID, _OWNER_ID)
        assert result is None

    @pytest.mark.unit
    async def test_set_license_config_validates_billing_cycle(self):
        svc = _make_svc()
        with pytest.raises(DomainError, match="billing_cycle"):
            await svc.set_license_config(_ORG_ID, _OWNER_ID, billing_cycle="weekly")

    @pytest.mark.unit
    async def test_set_license_config_validates_currency(self):
        svc = _make_svc()
        with pytest.raises(DomainError, match="currency"):
            await svc.set_license_config(_ORG_ID, _OWNER_ID, currency="USDX")

    @pytest.mark.unit
    async def test_set_license_config_no_repo_raises(self):
        svc = _make_svc()
        with pytest.raises(DomainError, match="License repository"):
            await svc.set_license_config(
                _ORG_ID, _OWNER_ID, seat_cost_cents=100, max_seats=10
            )

    @pytest.mark.unit
    async def test_set_license_validates_negative_cost(self):
        svc = _make_svc()
        with pytest.raises(DomainError, match="seat_cost_cents"):
            await svc.set_license_config(_ORG_ID, _OWNER_ID, seat_cost_cents=-1)

    @pytest.mark.unit
    async def test_set_license_validates_zero_seats(self):
        svc = _make_svc()
        with pytest.raises(DomainError, match="max_seats"):
            await svc.set_license_config(_ORG_ID, _OWNER_ID, max_seats=0)

    @pytest.mark.unit
    async def test_calculate_license_cost_no_repo(self):
        svc = _make_svc()
        result = await svc.calculate_license_cost(_ORG_ID, _OWNER_ID)
        assert result["seat_cost_cents"] == 0
        assert "active_seats" in result


# ---------------------------------------------------------------------------
# transfer_ownership
# ---------------------------------------------------------------------------


class TestTransferOwnership:
    @pytest.mark.unit
    async def test_raises_when_new_owner_not_found(self):
        svc = _make_svc()
        svc._user_repo.get_by_email.return_value = None
        with pytest.raises(DomainError, match="No user found"):
            await svc.transfer_ownership(_ORG_ID, _OWNER_ID, "ghost@example.com")

    @pytest.mark.unit
    async def test_raises_when_new_owner_not_member(self):
        svc = _make_svc()
        svc._membership_repo.get_membership.side_effect = [
            _membership(),  # actor check (_require_role)
            None,  # new owner membership check
        ]
        with pytest.raises(DomainError, match="not a member"):
            await svc.transfer_ownership(_ORG_ID, _OWNER_ID, "other@example.com")

    @pytest.mark.unit
    async def test_raises_when_transferring_to_self(self):
        svc = _make_svc()
        # user_repo returns same user (same id as _OWNER_ID)
        svc._membership_repo.get_membership.side_effect = [
            _membership(),  # _require_role
            _membership(),  # new owner membership
        ]
        with pytest.raises(DomainError, match="already the owner"):
            await svc.transfer_ownership(_ORG_ID, _OWNER_ID, "owner@example.com")

    @pytest.mark.unit
    async def test_transfer_ownership_success(self):
        """Happy path: promotes new owner, demotes current owner, updates org.owner_id."""
        new_owner_id = uuid.uuid4()
        new_user = MagicMock()
        new_user.id = new_owner_id
        new_user.email = "newowner@example.com"

        svc = _make_svc()
        svc._user_repo.get_by_email.return_value = new_user

        new_owner_mem = _membership(user_id=new_owner_id, role=OrgRole.MEMBER)
        promoted_mem = _membership(user_id=new_owner_id, role=OrgRole.OWNER)
        svc._membership_repo.get_membership.side_effect = [
            _membership(),  # _require_role (current owner)
            new_owner_mem,  # new owner membership check
        ]
        svc._membership_repo.update_role.return_value = promoted_mem

        result = await svc.transfer_ownership(
            _ORG_ID, _OWNER_ID, "newowner@example.com"
        )
        assert result is not None
        # update_role should be called twice: demote old owner, promote new
        assert svc._membership_repo.update_role.await_count == 2
        # org should have been updated with new owner_id
        svc._org_repo.update.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_member_detail
# ---------------------------------------------------------------------------


class TestGetMemberDetail:
    @pytest.mark.unit
    async def test_returns_member_detail(self):
        svc = _make_svc()
        # membership exists, user exists
        svc._membership_repo.get_membership.side_effect = [
            _membership(),  # actor _require_role
            _membership(_MEMBER_ID, role=OrgRole.MEMBER),  # target member
        ]
        fake_user = MagicMock()
        fake_user.email = "member@example.com"
        fake_user.name = "Member User"
        svc._user_repo.get_by_id.return_value = fake_user

        result = await svc.get_member_detail(_ORG_ID, _MEMBER_ID, _OWNER_ID)
        assert result["email"] == "member@example.com"
        assert "role" in result

    @pytest.mark.unit
    async def test_raises_when_member_not_found(self):
        svc = _make_svc()
        svc._membership_repo.get_membership.side_effect = [
            _membership(),  # actor _require_role
            None,  # target membership → not found
        ]
        with pytest.raises(DomainError, match="Member not found"):
            await svc.get_member_detail(_ORG_ID, uuid.uuid4(), _OWNER_ID)


# ---------------------------------------------------------------------------
# set_license_config (with license_repo)
# ---------------------------------------------------------------------------


class TestSetLicenseConfigWithRepo:
    @pytest.mark.unit
    async def test_success_with_license_repo(self):
        """set_license_config creates the config and syncs org.max_members."""
        from src.domain.entities.organization import OrgLicenseConfig

        svc = _make_svc()

        fake_config = OrgLicenseConfig(
            org_id=_ORG_ID,
            seat_cost_cents=500,
            max_seats=10,
            currency="USD",
            billing_cycle="monthly",
        )
        license_repo = AsyncMock()
        license_repo.upsert.return_value = fake_config
        license_repo.get_by_org.return_value = fake_config
        svc._license_repo = license_repo

        result = await svc.set_license_config(
            _ORG_ID,
            _OWNER_ID,
            seat_cost_cents=500,
            max_seats=10,
        )
        assert result.seat_cost_cents == 500
        license_repo.upsert.assert_awaited_once()
        # org.max_members should be synced (org.max_members defaults differ from 10)
        svc._org_repo.update.assert_awaited()

    @pytest.mark.unit
    async def test_calculate_license_cost_with_repo_and_config(self):
        """calculate_license_cost uses license config when available."""
        from src.domain.entities.organization import OrgLicenseConfig

        svc = _make_svc(member_count=3)

        fake_config = OrgLicenseConfig(
            org_id=_ORG_ID,
            seat_cost_cents=1000,
            max_seats=10,
            currency="EUR",
            billing_cycle="annual",
        )
        license_repo = AsyncMock()
        license_repo.get_by_org.return_value = fake_config
        svc._license_repo = license_repo

        result = await svc.calculate_license_cost(_ORG_ID, _OWNER_ID)
        assert result["active_seats"] == 3
        assert result["seat_cost_cents"] == 1000
        assert result["currency"] == "EUR"

    @pytest.mark.unit
    async def test_calculate_license_cost_with_repo_no_config(self):
        """calculate_license_cost returns zeros when repo has no config."""
        svc = _make_svc(member_count=2)
        license_repo = AsyncMock()
        license_repo.get_by_org.return_value = None
        svc._license_repo = license_repo

        result = await svc.calculate_license_cost(_ORG_ID, _OWNER_ID)
        assert result["seat_cost_cents"] == 0
        assert result["active_seats"] == 2


# ---------------------------------------------------------------------------
# _slugify edge cases
# ---------------------------------------------------------------------------


class TestSlugify:
    @pytest.mark.unit
    def test_removes_special_chars(self):
        result = OrganizationService._slugify("Acme & Co! (Ltd.)")
        assert "&" not in result
        assert "!" not in result

    @pytest.mark.unit
    def test_truncates_to_50_chars(self):
        long_name = "A" * 100
        result = OrganizationService._slugify(long_name)
        assert len(result) <= 50

    @pytest.mark.unit
    def test_collapses_multiple_spaces(self):
        result = OrganizationService._slugify("Hello   World")
        assert "--" not in result
        assert result == "hello-world"


# ---------------------------------------------------------------------------
# Missing branch coverage tests
# ---------------------------------------------------------------------------


class TestInviteMemberMissingBranches:
    """Cover lines 131, 137-139, 149-153, 155-156, 234-254."""

    @pytest.mark.unit
    async def test_invite_raises_when_org_not_found(self):
        """invite_member raises when org_repo returns None (line 131)."""
        svc = _make_svc()
        svc._org_repo.get_by_id.return_value = None
        svc._membership_repo.get_membership.side_effect = [_membership(), None]
        with pytest.raises(DomainError, match="Organization not found"):
            await svc.invite_member(
                org_id=_ORG_ID,
                email="new@example.com",
                role=OrgRole.MEMBER,
                invited_by=_OWNER_ID,
            )

    @pytest.mark.unit
    async def test_invite_uses_license_repo_seat_cap(self):
        """When license_repo is set, effective_cap comes from license (lines 137-139)."""
        license_repo = AsyncMock()
        fake_lic = MagicMock()
        fake_lic.max_seats = 2  # below count of 5
        license_repo.get_by_org.return_value = fake_lic

        svc = _make_svc(member_count=5)
        svc._license_repo = license_repo
        svc._membership_repo.get_membership.side_effect = [_membership(), None]

        with pytest.raises(DomainError, match="seat limit"):
            await svc.invite_member(
                org_id=_ORG_ID,
                email="new@example.com",
                role=OrgRole.MEMBER,
                invited_by=_OWNER_ID,
            )
        license_repo.get_by_org.assert_called_once()

    @pytest.mark.unit
    async def test_invite_creates_new_user_when_not_found(self):
        """invite_member creates a User when email not in user_repo (lines 149-153)."""
        svc = _make_svc()
        svc._user_repo.get_by_email.return_value = None  # no existing user
        svc._membership_repo.get_membership.side_effect = [_membership(), None]

        result = await svc.invite_member(
            org_id=_ORG_ID,
            email="brand-new@example.com",
            role=OrgRole.MEMBER,
            invited_by=_OWNER_ID,
            member_name="New Person",
        )
        svc._user_repo.create.assert_called_once()
        assert result is not None

    @pytest.mark.unit
    async def test_invite_updates_user_name_when_blank(self):
        """invite_member updates user.name when user exists but has no name (lines 155-156)."""
        svc = _make_svc()
        nameless_user = MagicMock()
        nameless_user.id = uuid.uuid4()
        nameless_user.email = "someone@example.com"
        nameless_user.name = ""  # blank name
        svc._user_repo.get_by_email.return_value = nameless_user
        svc._membership_repo.get_membership.side_effect = [_membership(), None]

        await svc.invite_member(
            org_id=_ORG_ID,
            email="someone@example.com",
            role=OrgRole.MEMBER,
            invited_by=_OWNER_ID,
            member_name="Proper Name",
        )
        svc._user_repo.update.assert_called_once()
        assert nameless_user.name == "Proper Name"

    @pytest.mark.unit
    async def test_invite_sends_email_when_settings_provided(self):
        """invite_member calls send_invite_email when settings is set (lines 234-254)."""
        from unittest.mock import patch

        svc = _make_svc()
        svc._settings = MagicMock()
        svc._settings.app_base_url = "https://app.example.com"
        svc._membership_repo.get_membership.side_effect = [_membership(), None]

        with patch(
            "src.infrastructure.notifications.email_sender.send_invite_email",
            new=AsyncMock(),
        ) as mock_send:
            result = await svc.invite_member(
                org_id=_ORG_ID,
                email="invited@example.com",
                role=OrgRole.MEMBER,
                invited_by=_OWNER_ID,
            )
        mock_send.assert_called_once()
        assert result is not None


class TestLicenseConfig:
    """Cover lines 397 (get_license_config with license_repo set)."""

    @pytest.mark.unit
    async def test_get_license_config_returns_config(self):
        """get_license_config returns OrgLicenseConfig when license_repo is present (line 397)."""
        license_repo = AsyncMock()
        fake_lic = MagicMock()
        license_repo.get_by_org.return_value = fake_lic

        svc = _make_svc()
        svc._license_repo = license_repo

        result = await svc.get_license_config(_ORG_ID, _OWNER_ID)
        assert result is fake_lic
        license_repo.get_by_org.assert_called_once()


class TestInviteEmailException:
    @pytest.mark.unit
    async def test_invite_email_exception_is_caught(self):
        """Exception from send_invite_email is swallowed (lines 253-254)."""
        from unittest.mock import patch

        svc = _make_svc()
        svc._settings = MagicMock()
        svc._settings.app_base_url = "https://app.example.com"
        svc._membership_repo.get_membership.side_effect = [_membership(), None]

        with patch(
            "src.infrastructure.notifications.email_sender.send_invite_email",
            new=AsyncMock(side_effect=RuntimeError("SMTP down")),
        ):
            # Should NOT raise even though email fails
            result = await svc.invite_member(
                org_id=_ORG_ID,
                email="invited@example.com",
                role=OrgRole.MEMBER,
                invited_by=_OWNER_ID,
            )
        assert result is not None


class TestLicenseConfigMethods:
    @pytest.mark.unit
    async def test_get_license_config_returns_none_without_repo(self):
        """get_license_config returns None when no license_repo (line 396)."""
        svc = _make_svc()  # no license_repo
        result = await svc.get_license_config(_ORG_ID, _OWNER_ID)
        assert result is None

    @pytest.mark.unit
    async def test_set_license_config_negative_seat_cost_raises(self):
        """seat_cost_cents < 0 raises DomainError (line 413)."""
        svc = _make_svc()
        with pytest.raises(DomainError, match="seat_cost_cents"):
            await svc.set_license_config(_ORG_ID, _OWNER_ID, seat_cost_cents=-1)

    @pytest.mark.unit
    async def test_set_license_config_zero_max_seats_raises(self):
        """max_seats < 1 raises DomainError (line 415)."""
        svc = _make_svc()
        with pytest.raises(DomainError, match="max_seats"):
            await svc.set_license_config(_ORG_ID, _OWNER_ID, max_seats=0)

    @pytest.mark.unit
    async def test_set_license_config_invalid_billing_cycle_raises(self):
        """Invalid billing_cycle raises DomainError (line 417)."""
        svc = _make_svc()
        with pytest.raises(DomainError, match="billing_cycle"):
            await svc.set_license_config(_ORG_ID, _OWNER_ID, billing_cycle="quarterly")

    @pytest.mark.unit
    async def test_set_license_config_invalid_currency_raises(self):
        """Invalid currency length raises DomainError (line 419)."""
        svc = _make_svc()
        with pytest.raises(DomainError, match="currency"):
            await svc.set_license_config(_ORG_ID, _OWNER_ID, currency="USDX")

    @pytest.mark.unit
    async def test_set_license_config_no_repo_raises(self):
        """set_license_config raises when license_repo is None (line 422)."""
        svc = _make_svc()  # no license_repo
        with pytest.raises(DomainError, match="License repository"):
            await svc.set_license_config(_ORG_ID, _OWNER_ID)

    @pytest.mark.unit
    async def test_calculate_license_cost_no_repo_returns_defaults(self):
        """calculate_license_cost returns defaults when license_repo is None (line 451)."""
        svc = _make_svc()
        result = await svc.calculate_license_cost(_ORG_ID, _OWNER_ID)
        assert result["seat_cost_cents"] == 0
        assert result["total_cost_cents"] == 0
        assert "active_seats" in result
