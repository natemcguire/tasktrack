import unittest
from types import SimpleNamespace

from tasktrack.accounts import digest, timestamp, token


class EmailSignInTests(unittest.IsolatedAsyncioTestCase):
    """Standalone (non-SSO) email sign-in: who lands in which workspace."""

    def setUp(self):
        from tests.test_agent_auth import AgentTests

        AgentTests.setUp(self)

    async def signed_in(self, email, next_path="/"):
        secret = token()
        await self.accounts.run(
            "INSERT INTO login_links VALUES (?,?,?,?)",
            digest(secret),
            email,
            next_path,
            timestamp() + 60,
        )
        session, destination = await self.accounts.redeem(secret, "ip")
        request = SimpleNamespace(headers={"Cookie": "tt_session=" + session})
        return await self.accounts.authenticate(request), destination

    async def test_new_email_gets_its_own_workspace_as_admin(self):
        identity, destination = await self.signed_in("new@example.com", "/account")
        self.assertEqual(destination, "/account")
        self.assertNotEqual(identity["workspace_id"], "w")
        self.assertEqual(identity["workspace_name"], "new’s workspace")
        self.assertEqual(
            (identity["access_role"], identity["kind"]), ("admin", "internal")
        )
        self.assertIsNone(
            await self.accounts.member_identity(identity["user_id"], "w")
        )

    async def test_returning_email_reopens_its_workspace_without_a_second_one(self):
        first, _ = await self.signed_in("new@example.com")
        again, _ = await self.signed_in("new@example.com")
        self.assertEqual(again["user_id"], first["user_id"])
        self.assertEqual(again["workspace_id"], first["workspace_id"])
        self.assertEqual(len((await self.accounts.overview(again))["workspaces"]), 1)

    async def test_existing_member_lands_in_their_existing_workspace(self):
        identity, _ = await self.signed_in("u@example.com")
        self.assertEqual(identity["user_id"], "u")
        self.assertEqual(identity["workspace_id"], "w")
        self.assertEqual(
            [w["id"] for w in (await self.accounts.overview(identity))["workspaces"]],
            ["w"],
        )

    async def test_a_sign_in_link_works_once(self):
        from tasktrack.db import Error

        secret = token()
        await self.accounts.run(
            "INSERT INTO login_links VALUES (?,?,?,?)",
            digest(secret),
            "new@example.com",
            "/",
            timestamp() + 60,
        )
        await self.accounts.redeem(secret, "ip")
        with self.assertRaises(Error) as refused:
            await self.accounts.redeem(secret, "ip")
        self.assertEqual(refused.exception.status, 400)
