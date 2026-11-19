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

        async def batch(statements):
            # D1 returns each statement's rows; accepting an invitation reads them.
            with self.c:
                return [
                    {"results": [dict(r) for r in self.c.execute(sql, args).fetchall()]}
                    for sql, args in statements
                ]

        self.accounts.batch = batch
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

    async def signed_in(self, email):
        """Sign in through SSO and return the identity the next request would get."""
        secret = await sso.create_session(self.accounts, self.exchange(email))
        request = SimpleNamespace(headers={"Cookie": "tt_session=" + secret})
        claims = {
            "active": True,
            "tenant_id": sso.TENANT,
            "products": [sso.PRODUCT],
            "email": email,
        }
        with patch.object(sso, "hub", AsyncMock(return_value=claims)):
            return await self.accounts.authenticate(request)

    async def test_new_person_gets_their_own_workspace_apart_from_the_owner(self):
        identity = await self.signed_in("new@example.com")
        self.assertNotEqual(identity["workspace_id"], "w")
        self.assertEqual(
            (identity["access_role"], identity["kind"]), ("admin", "internal")
        )
        self.accounts.owner(identity)  # may administer their own workspace
        overview = await self.accounts.overview(identity)
        self.assertEqual(
            [w["id"] for w in overview["workspaces"]], [identity["workspace_id"]]
        )
        self.assertEqual(
            [m["email"] for m in overview["members"]], ["new@example.com"]
        )
        # The owner's workspace is closed to them: not a member, cannot switch in.
        self.assertIsNone(
            await self.accounts.member_identity(identity["user_id"], "w")
        )
        with self.assertRaises(Error) as denied:
            await self.accounts.switch(identity, "w")
        self.assertEqual(denied.exception.status, 404)
        # The owner's member list is unchanged by the sign-up.
        self.assertEqual(
            [
                m["email"]
                for m in (await self.accounts.overview(self.identity))["members"]
            ],
            [sso.OWNER],
        )

    async def test_signing_in_again_reopens_the_same_personal_workspace(self):
        first = await self.signed_in("new@example.com")
        second = await self.signed_in("new@example.com")
        self.assertEqual(second["workspace_id"], first["workspace_id"])
        self.assertEqual(second["user_id"], first["user_id"])
        self.assertEqual(
            len((await self.accounts.overview(second))["workspaces"]), 1
        )

    async def test_two_new_people_do_not_share_a_workspace(self):
        one = await self.signed_in("one@example.com")
        two = await self.signed_in("two@example.com")
        self.assertNotEqual(one["workspace_id"], two["workspace_id"])
        self.assertIsNone(
            await self.accounts.member_identity(two["user_id"], one["workspace_id"])
        )

    async def test_existing_member_still_lands_in_their_existing_workspace(self):
        self.c.execute(
            "INSERT INTO users VALUES('m','member@example.com','Member',2)"
        )
        self.c.execute(
            "INSERT INTO memberships(workspace_id,user_id,role,created_at) VALUES('w','m','member',2)"
        )
        identity = await self.signed_in("member@example.com")
        self.assertEqual(identity["user_id"], "m")
        self.assertEqual(identity["workspace_id"], "w")
        self.assertEqual(identity["access_role"], "regular")
        self.assertEqual(
            [w["id"] for w in (await self.accounts.overview(identity))["workspaces"]],
            ["w"],
        )

    async def test_signed_up_person_can_still_accept_a_workspace_invitation(self):
        person = await self.signed_in("new@example.com")
        own = person["workspace_id"]
        link = await self.accounts.invite(self.identity)
        await self.accounts.join(person, link.rsplit("/", 1)[1])
        invited = await self.accounts.member_identity(person["user_id"], "w")
        self.assertEqual(invited["access_role"], "regular")
        self.assertEqual(
            {w["id"] for w in (await self.accounts.overview(person))["workspaces"]},
            {own, "w"},
        )

    async def test_signup_opens_account_creation_and_login_opens_sign_in(self):
        from urllib.parse import parse_qs, urlsplit

        request = SimpleNamespace(method="GET", headers={})
        sent = []

        def redirect(url, cookie=None):
            sent.append(url)
            return url

        async def hub_query(path, query):
            """Follow /login or /signup to the hub and return the hub URL's query."""
            start = urlsplit(
                await sso.route(request, self.accounts, path, query, None, None, redirect)
            )
            self.assertEqual(start.path, "/auth/sso/start")
            forwarded = {k: v[0] for k, v in parse_qs(start.query).items()}
            hub = urlsplit(
                await sso.route(
                    request,
                    self.accounts,
                    start.path,
                    forwarded,
                    None,
                    None,
                    redirect,
                )
            )
            self.assertEqual(hub.netloc, "auth.nates-software.com")
            self.assertEqual(hub.path, "/authorize")
            return {k: v[0] for k, v in parse_qs(hub.query).items()}

        with patch.dict("sys.modules", {"workers": SimpleNamespace(Response=None)}):
            signup = await hub_query("/signup", {"next": "/account"})
            login = await hub_query("/login", {"next": "/account"})
        self.assertEqual(signup["intent"], "signup")
        self.assertNotIn("intent", login)
        for sent_query in (signup, login):
            self.assertEqual(sent_query["app"], "tasktrack")
            self.assertEqual(
                sent_query["return"], "http://localhost:8787/auth/sso/callback"
            )
            # The return path chosen before sign-up survives the round trip.
            saved = await self.accounts.one(
                "SELECT next_path FROM sso_states WHERE state_hash=?",
                digest(sent_query["state"]),
            )
            self.assertEqual(saved["next_path"], "/account")

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

    def test_host_branding_and_canonical_links_keep_callback_origin(self):
        from pathlib import Path

        from tasktrack.hosted_pages import brand_html, page

        for source in [
            page("Account", "<p>Tasktrack user text</p>"),
            Path("tasktrack/static/index.html").read_text(),
        ]:
            eastbay = brand_html(source, "tasks.eastbayprojects.com")
            canonical = brand_html(source, "tasks.nates-software.com")
            self.assertIn("Tasks · East Bay Projects</title>", eastbay)
            self.assertIn("</span>East Bay Projects</a", eastbay)
            self.assertIn("Tasks</title>", canonical)
            self.assertIn("</span>Tasks</a", canonical)
            self.assertEqual(brand_html(source, "localhost"), source)
        self.accounts.env.PUBLIC_URL = "https://tasks.nates-software.com"
        self.accounts.origin = "https://tasks.eastbayprojects.com"
        self.assertEqual(self.accounts.link_origin, "https://tasks.nates-software.com")
        self.assertEqual(self.accounts.origin, "https://tasks.eastbayprojects.com")

    async def test_global_logout_uses_hub_session_and_requires_success(self):
        secret = await sso.create_session(self.accounts, self.exchange())
        identity = {"session_hash": digest(secret)}
        with patch.object(sso, "hub", AsyncMock(return_value={"ok": True})) as call:
            self.assertEqual(
                await sso.logout(self.accounts, identity),
                "https://auth.nates-software.com/signed-out?app=tasktrack",
            )
            call.assert_awaited_once_with(
                self.accounts,
                "/session/logout",
                {"token": "hub-token", "audience": "tasktrack"},
            )
        with patch.object(sso, "hub", AsyncMock(return_value={})):
            with self.assertRaises(Error):
                await sso.logout(self.accounts, identity)
        self.assertIsNotNone(
            await self.accounts.one(
                "SELECT 1 FROM sessions WHERE token_hash=?", digest(secret)
            )
        )
