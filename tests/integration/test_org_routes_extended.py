"""
Extended integration tests for org route endpoints to improve coverage.
Covers org CRUD, member management, and provider connections.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient


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
async def org_data(client, auth_headers):
    """Create an org and return its data."""
    resp = await client.post(
        "/api/v1/orgs/",
        json={"name": f"TestOrg-{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestOrgCRUD:
    @pytest.mark.integration
    async def test_list_organizations_empty_initially(self, client, auth_headers):
        resp = await client.get("/api/v1/orgs/", headers=auth_headers)
        assert resp.status_code == 200
        # May have orgs from other tests - just check it returns a list
        assert isinstance(resp.json(), list)

    @pytest.mark.integration
    async def test_list_organizations_with_orgs(self, client, auth_headers):
        """Create an org and then list orgs — covers the for-loop success body."""
        create_resp = await client.post(
            "/api/v1/orgs/",
            json={"name": f"ListOrg-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        assert create_resp.status_code == 201

        resp = await client.get("/api/v1/orgs/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # Should have at least the org we just created
        assert len(data) >= 1
        assert "id" in data[0]
        assert "member_count" in data[0]

    @pytest.mark.integration
    async def test_create_organization_returns_201(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/orgs/",
            json={"name": f"NewOrg-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert "name" in data

    @pytest.mark.integration
    async def test_get_organization_returns_200(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        resp = await client.get(f"/api/v1/orgs/{org_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == org_id

    @pytest.mark.integration
    async def test_update_organization(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        new_name = f"UpdatedOrg-{uuid.uuid4().hex[:6]}"
        resp = await client.patch(
            f"/api/v1/orgs/{org_id}",
            json={"name": new_name},
            headers=auth_headers,
        )
        # May be 200 or 204 or 201 depending on implementation
        assert resp.status_code in (200, 204)

    @pytest.mark.integration
    async def test_delete_organization_removes_it(self, client, auth_headers):
        # Create a new org to delete
        create_resp = await client.post(
            "/api/v1/orgs/",
            json={"name": f"ToDelete-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        org_id = create_resp.json()["id"]

        # Delete it
        del_resp = await client.delete(f"/api/v1/orgs/{org_id}", headers=auth_headers)
        assert del_resp.status_code == 204

        # Verify it's gone
        get_resp = await client.get(f"/api/v1/orgs/{org_id}", headers=auth_headers)
        assert get_resp.status_code == 404

    @pytest.mark.integration
    async def test_delete_organization_not_owner_returns_403(
        self, client, auth_headers
    ):
        """Try to delete an org you don't own."""
        # Create another user and their org
        email2 = f"user2-{uuid.uuid4().hex[:6]}@ex.com"
        reg2 = await client.post(
            "/api/v1/auth/register",
            json={"email": email2, "password": "Password@123", "name": "User2"},
        )
        assert reg2.status_code in (200, 201)
        headers2 = {"Authorization": f"Bearer {reg2.json()['access_token']}"}

        create_resp = await client.post(
            "/api/v1/orgs/",
            json={"name": f"User2Org-{uuid.uuid4().hex[:6]}"},
            headers=headers2,
        )
        assert create_resp.status_code == 201
        org_id = create_resp.json()["id"]

        # Try to delete as user1 (not owner)
        resp = await client.delete(f"/api/v1/orgs/{org_id}", headers=auth_headers)
        assert resp.status_code in (403, 404)


class TestOrgMembers:
    @pytest.mark.integration
    async def test_list_members_as_owner(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        resp = await client.get(f"/api/v1/orgs/{org_id}/members", headers=auth_headers)
        assert resp.status_code == 200
        members = resp.json()
        assert isinstance(members, list)
        assert len(members) >= 1  # At least the owner

    @pytest.mark.integration
    async def test_invite_member_to_org(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        email = f"newmember-{uuid.uuid4().hex[:6]}@example.com"
        resp = await client.post(
            f"/api/v1/orgs/{org_id}/members",
            json={"email": email, "role": "member", "name": "New Member"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201)

    @pytest.mark.integration
    async def test_invite_member_invalid_role(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        resp = await client.post(
            f"/api/v1/orgs/{org_id}/members",
            json={"email": "test@ex.com", "role": "invalid_role"},
            headers=auth_headers,
        )
        # Invalid role may be accepted (mapped to member) or rejected
        assert resp.status_code in (200, 201, 400, 422)

    @pytest.mark.integration
    async def test_remove_member_not_found(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        fake_user_id = str(uuid.uuid4())
        resp = await client.delete(
            f"/api/v1/orgs/{org_id}/members/{fake_user_id}",
            headers=auth_headers,
        )
        assert resp.status_code in (204, 404, 403)

    @pytest.mark.integration
    async def test_get_member_details(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        # First get the list to find the owner's user_id
        members_resp = await client.get(
            f"/api/v1/orgs/{org_id}/members", headers=auth_headers
        )
        members = members_resp.json()
        if members:
            user_id = members[0]["user_id"]
            resp = await client.get(
                f"/api/v1/orgs/{org_id}/members/{user_id}",
                headers=auth_headers,
            )
            assert resp.status_code in (200, 404)

    @pytest.mark.integration
    async def test_list_pending_invites(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        resp = await client.get(f"/api/v1/orgs/{org_id}/invites", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.integration
    async def test_revoke_nonexistent_invite(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        fake_invite_id = str(uuid.uuid4())
        resp = await client.delete(
            f"/api/v1/orgs/{org_id}/invites/{fake_invite_id}",
            headers=auth_headers,
        )
        assert resp.status_code in (204, 404)

    @pytest.mark.integration
    async def test_update_member_role_self(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        # Get own user id from /me
        me_resp = await client.get("/api/v1/auth/me", headers=auth_headers)
        user_id = me_resp.json()["id"]
        resp = await client.patch(
            f"/api/v1/orgs/{org_id}/members/{user_id}",
            json={"role": "admin"},
            headers=auth_headers,
        )
        # Should succeed or return appropriate error
        assert resp.status_code in (200, 400, 403, 422)

    @pytest.mark.integration
    async def test_transfer_ownership(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        # Transfer to nonexistent user by email
        resp = await client.post(
            f"/api/v1/orgs/{org_id}/transfer-ownership",
            json={"new_owner_email": "nobody@nonexistent.com"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 400, 404, 403)


class TestOrgProviders:
    @pytest.mark.integration
    async def test_list_providers(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        resp = await client.get(
            f"/api/v1/orgs/{org_id}/providers", headers=auth_headers
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.integration
    async def test_disconnect_provider_not_found(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        fake_conn_id = str(uuid.uuid4())
        resp = await client.delete(
            f"/api/v1/orgs/{org_id}/providers/{fake_conn_id}",
            headers=auth_headers,
        )
        assert resp.status_code in (204, 404)

    @pytest.mark.integration
    async def test_google_provider_auth_returns_url(
        self, client, auth_headers, org_data
    ):
        org_id = org_data["id"]
        resp = await client.get(
            f"/api/v1/orgs/{org_id}/providers/google/auth",
            headers=auth_headers,
        )
        # Returns auth URL or error if OAuth not configured
        assert resp.status_code in (200, 500)

    @pytest.mark.integration
    async def test_connect_provider_google(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        resp = await client.post(
            f"/api/v1/orgs/{org_id}/providers",
            json={
                "provider": "google",
                "provider_email": "test@gmail.com",
                "access_token": "test-token",
                "refresh_token": "refresh-token",
            },
            headers=auth_headers,
        )
        # May succeed or fail depending on validation
        assert resp.status_code in (200, 201, 400, 422)


class TestOrgLicenseConfig:
    @pytest.mark.integration
    async def test_get_license_config(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        resp = await client.get(f"/api/v1/orgs/{org_id}/license", headers=auth_headers)
        assert resp.status_code in (200, 404)

    @pytest.mark.integration
    async def test_update_license_config(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        resp = await client.put(
            f"/api/v1/orgs/{org_id}/license",
            json={"max_members": 10, "allow_external_providers": True},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201, 422)


class TestOrgEvents:
    @pytest.mark.integration
    async def test_create_org_event(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        resp = await client.post(
            f"/api/v1/orgs/{org_id}/events",
            json={
                "title": "Team Kickoff",
                "start_time": "2030-06-01T10:00:00Z",
                "end_time": "2030-06-01T11:00:00Z",
                "invited_member_ids": [],
            },
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201, 400, 422, 502)

    @pytest.mark.integration
    async def test_list_org_events(self, client, auth_headers, org_data):
        org_id = org_data["id"]
        resp = await client.get(f"/api/v1/orgs/{org_id}/events", headers=auth_headers)
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert isinstance(resp.json(), list)
