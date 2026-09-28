import unittest
from types import SimpleNamespace

from tasktrack import service_apps as apps
from tasktrack.accounts import digest
from tasktrack.db import Error


class ServiceAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.test_agent_auth import AgentTests

        AgentTests.setUp(self)

    async def test_credentials_are_workspace_owned_and_rotation_revokes_tokens(self):
        result = await apps.create(
            self.accounts,
            self.identity,
            {
                "name": "Internal dashboard",
                "project_ids": [1],
                "capabilities": ["project.read", "work.read"],
            },
        )
        grant = await apps.exchange(self.accounts, result, "ip")
        token = grant["access_token"]
        who = await self.accounts.authenticate(
            SimpleNamespace(headers={"Authorization": "Bearer " + token})
        )
        self.assertEqual(who["token_project_ids"], ["1"])
        self.assertEqual(who["access_role"], "regular")
        self.assertNotEqual(who["user_id"], self.identity["user_id"])
        # Removing the creator does not remove a workspace-owned app.
        self.c.execute(
            "INSERT INTO users VALUES('u2','u2@example.com','Other admin',1)"
        )
        self.c.execute(
            "INSERT INTO memberships(workspace_id,user_id,role,created_at) VALUES('w','u2','owner',1)"
        )
        self.c.execute("DELETE FROM memberships WHERE user_id='u'")
        self.assertIsNotNone(await apps.authenticate(self.accounts, token))
        row = await apps.get(self.accounts, self.identity, result["client_id"])
        rotated = await apps.change(self.accounts, self.identity, row, "rotate")
        self.assertIsNone(await apps.authenticate(self.accounts, token))
        self.assertEqual(
            (await apps.exchange(self.accounts, result, "ip"))["error"],
            "invalid_client",
        )
        new = await apps.exchange(self.accounts, rotated, "ip")
        self.assertIn("access_token", new)
        row = await apps.get(self.accounts, self.identity, result["client_id"])
        await apps.change(self.accounts, self.identity, row, "revoke")
        self.assertIsNone(await apps.authenticate(self.accounts, new["access_token"]))

    async def test_admin_scope_and_cross_workspace_denials(self):
        with self.assertRaises(Error):
            await apps.create(self.accounts, self.identity | {"bearer": True}, {})
        with self.assertRaises(Error):
            apps.definition(
                {
                    "name": "Bad",
                    "project_ids": [1],
                    "capabilities": ["project.read", "work.read", "work.edit"],
                }
            )
        result = await apps.create(
            self.accounts,
            self.identity,
            {
                "name": "Good",
                "project_ids": [1],
                "capabilities": ["project.read", "work.read"],
            },
        )
        with self.assertRaises(Error):
            await apps.get(
                self.accounts,
                self.identity | {"workspace_id": "other"},
                result["client_id"],
            )
        self.assertEqual(
            (
                await apps.exchange(
                    self.accounts, result | {"client_secret": "wrong"}, "ip"
                )
            )["error"],
            "invalid_client",
        )
        row = await apps.get(self.accounts, self.identity, result["client_id"])
        self.assertEqual(row["secret_hash"], digest(result["client_secret"]))
        self.assertNotIn("secret_hash", apps.public(row))

    async def test_project_grants_are_explicit_and_app_cannot_write(self):
        import tempfile
        import uuid

        from tasktrack.access import Access
        from tasktrack.db import Store
        from tasktrack.service import Service

        with tempfile.TemporaryDirectory() as directory:
            admin = self.identity | {
                "membership_id": "m",
                "membership_revision": 1,
                "membership_capabilities": "[]",
                "status": "active",
            }
            service = Service(Store(directory), access=Access(admin))

            def create(key):
                return service.mutate(
                    "POST",
                    "/api/v1/projects",
                    {"key": key, "name": key},
                    "admin",
                    str(uuid.uuid4()),
                )[1]

            first, second = create("ONE"), create("TWO")
            with service.store.connection(write=True) as c:
                c.execute("UPDATE project_access SET internal_access='restricted'")
            credentials = await apps.create(
                self.accounts,
                self.identity,
                {
                    "name": "Restricted dashboard",
                    "project_ids": [first["id"]],
                    "capabilities": ["project.read", "work.read"],
                },
            )
            access = await apps.exchange(self.accounts, credentials, "app-project-test")
            who = await apps.authenticate(self.accounts, access["access_token"])
            service.access = Access(who)
            self.assertEqual(
                [p["id"] for p in service.read("/api/v1/projects")["items"]],
                [first["id"]],
            )
            with self.assertRaises(Error):
                service.read("/api/v1/projects/" + str(second["id"]))
            with self.assertRaises(Error):
                service.mutate(
                    "POST",
                    "/api/v1/tasks",
                    {"project_id": first["id"], "title": "Forbidden"},
                    who["actor"],
                    str(uuid.uuid4()),
                )
