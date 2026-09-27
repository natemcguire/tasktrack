import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tasktrack import sso
from tasktrack.accounts import digest
from tasktrack.db import Error


class SSOTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.test_agent_auth import AgentTests

        AgentTests.setUp(self)
        self.accounts.env.SSO_REQUIRED = "1"
        self.c.execute("UPDATE users SET email=? WHERE id='u'", (sso.OWNER,))

    def exchange(self, email=sso.OWNER):
        return {
            "user": {"id": "hub-user", "email": email, "name": "Nate"},
            "tenant_id": sso.TENANT,
            "products": [sso.PRODUCT],
            "token": "hub-token",
        }

    async def test_owner_email_migration_preserves_uuid_and_workspaces(self):
        from pathlib import Path

        self.c.execute("UPDATE users SET email='nate@eastbayprojects.com' WHERE id='u'")
        migration = Path("cloudflare/migrations/0013_sso.sql").read_text()
        self.c.executescript(migration[migration.index("UPDATE users") :])
        row = self.c.execute("SELECT id,email FROM users WHERE id='u'").fetchone()
        self.assertEqual(row["email"], sso.OWNER)
        self.assertEqual(
            self.c.execute("SELECT created_by FROM workspaces WHERE id='w'").fetchone()[
                0
            ],
            "u",
        )

    async def test_owner_preserves_existing_workspace_and_rejects_wrong_tenant(self):
        secret = await sso.create_session(self.accounts, self.exchange())
        row = self.c.execute(
            "SELECT * FROM sessions WHERE token_hash=?", (digest(secret),)
        ).fetchone()
        self.assertEqual(row["user_id"], "u")
        self.assertEqual(row["workspace_id"], "w")
        self.assertEqual(
            self.c.execute("SELECT count(*) FROM workspaces").fetchone()[0], 1
        )
        with self.assertRaises(Error):
            await sso.create_session(
                self.accounts, self.exchange() | {"tenant_id": "other"}
            )
        with self.assertRaises(Error):
            await sso.create_session(
                self.accounts, self.exchange() | {"products": ["view"]}
            )

    async def test_invited_user_joins_owner_workspace_without_admin(self):
        await sso.create_session(self.accounts, self.exchange("invited@example.com"))
        row = self.c.execute(
            "SELECT m.* FROM memberships m JOIN users u ON u.id=m.user_id WHERE u.email='invited@example.com'"
        ).fetchone()
        self.assertEqual(row["workspace_id"], "w")
        self.assertEqual(row["access_role"], "regular")

    async def test_live_revocation_and_legacy_session_denial(self):
        secret = await sso.create_session(self.accounts, self.exchange())
        request = SimpleNamespace(headers={"Cookie": "tt_session=" + secret})
        claims = {
            "active": True,
            "tenant_id": sso.TENANT,
            "products": [sso.PRODUCT],
            "email": sso.OWNER,
        }
        with patch.object(sso, "hub", AsyncMock(return_value=claims)):
            self.assertIsNotNone(await self.accounts.authenticate(request))
        with patch.object(sso, "hub", AsyncMock(return_value={"active": False})):
            self.assertIsNone(await self.accounts.authenticate(request))
        self.c.execute("DELETE FROM sso_sessions")
        self.assertIsNone(await self.accounts.authenticate(request))

    async def test_api_token_requires_live_product_grant(self):
        identity = {"bearer": True, "email": sso.OWNER}
        with patch.object(sso, "entitlement", AsyncMock(return_value=False)):
            self.assertIsNone(await sso.authorize_identity(self.accounts, identity))
        with patch.object(sso, "entitlement", AsyncMock(return_value=True)):
            self.assertEqual(
                await sso.authorize_identity(self.accounts, identity), identity
            )

    async def test_service_app_uses_creator_entitlement(self):
        from tasktrack import service_apps

        credentials = await service_apps.create(
            self.accounts,
            self.identity,
            {
                "name": "Read-only",
                "project_ids": [1],
                "capabilities": ["project.read", "work.read"],
            },
        )
        result = await service_apps.exchange(self.accounts, credentials, "ip")
        request = SimpleNamespace(
            headers={"Authorization": "Bearer " + result["access_token"]}
        )
        with patch.object(sso, "entitlement", AsyncMock(return_value=True)) as check:
            identity = await self.accounts.authenticate(request)
            self.assertEqual(identity["token_project_ids"], ["1"])
            check.assert_awaited_once_with(self.accounts, sso.OWNER)
        with patch.object(sso, "entitlement", AsyncMock(return_value=False)):
            self.assertIsNone(await self.accounts.authenticate(request))

    async def test_callback_state_is_bound_to_cookie_and_consumed_once(self):
        from tasktrack.accounts import timestamp

        state = "a" * 43
        await self.accounts.run(
            "INSERT INTO sso_states VALUES (?,?,?)",
            digest(state),
            "/account",
            timestamp() + 60,
        )
        request = SimpleNamespace(
            method="POST", headers={"Cookie": "__Host-tt_sso_state=" + state}
        )
        response = SimpleNamespace(json=lambda value, **kwargs: value)
        with patch.dict("sys.modules", {"workers": SimpleNamespace(Response=response)}):
            with patch.object(
                sso, "hub", AsyncMock(return_value=self.exchange())
            ) as exchange:
                with self.assertRaises(Error):
                    await sso.route(
                        request,
                        self.accounts,
                        "/auth/sso/callback",
                        {},
                        AsyncMock(return_value={"state": "wrong", "token": "login"}),
                        None,
                        None,
                    )
                exchange.assert_not_awaited()
                body = AsyncMock(return_value={"state": state, "token": "login"})
                result = await sso.route(
                    request, self.accounts, "/auth/sso/callback", {}, body, None, None
                )
                self.assertEqual(result["next"], "/account")
                with self.assertRaises(Error):
                    await sso.route(
                        request,
                        self.accounts,
                        "/auth/sso/callback",
                        {},
                        body,
                        None,
                        None,
                    )
                self.assertEqual(exchange.await_count, 1)
