import unittest
from unittest.mock import patch

from tasktrack.import_fetches import CheckpointClient, PauseFetch


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_pauses_and_resumes_without_refetching_or_mutating_saved_pages(self):
        calls = []

        async def fetch(_client, path, body=None):
            calls.append(path)
            return {"items": [{"id": path}]}, {}

        row = {"provider": "trello", "accounts": '[{"id":"a"}]'}
        secret = {"access_token": "not-a-real-secret"}
        cache = {}
        with patch("tasktrack.integrations.Client.get", fetch):
            first = CheckpointClient(row, secret, "a", cache, None, "test", budget=2)
            a, _ = await first.get("/1")
            a["items"].append({"id": "changed locally"})
            await first.get("/2")
            with self.assertRaises(PauseFetch):
                await first.get("/3")
            second = CheckpointClient(row, secret, "a", cache, None, "test", budget=2)
            a, _ = await second.get("/1")
            self.assertEqual(len(a["items"]), 1)
            await second.get("/2")
            await second.get("/3")
        self.assertEqual(calls, ["/1", "/2", "/3"])

    async def test_download_checkpoint_does_not_repeat_provider_fetch(self):
        from types import SimpleNamespace

        class Bucket:
            def __init__(self):
                self.files = {}

            async def put(self, key, content):
                self.files[key] = content

            async def get(self, key):
                content = self.files.get(key)
                if content is None:
                    return None

                async def arrayBuffer():
                    return SimpleNamespace(to_py=lambda: content)

                return SimpleNamespace(arrayBuffer=arrayBuffer)

        calls = []

        async def download(_client, url):
            calls.append(url)
            return b"original-file"

        cache = {}
        bucket = Bucket()
        row = {"provider": "trello", "accounts": '[{"id":"a"}]'}
        with patch("tasktrack.integrations.Client.download", download):
            first = CheckpointClient(
                row, {"access_token": "test"}, "a", cache, bucket, "private", budget=1
            )
            self.assertEqual(
                await first.download("https://trello.com/1/file"), b"original-file"
            )
            resumed = CheckpointClient(
                row, {"access_token": "test"}, "a", cache, bucket, "private", budget=1
            )
            self.assertEqual(
                await resumed.download("https://trello.com/1/file"), b"original-file"
            )
        self.assertEqual(len(calls), 1)

    async def test_checkpoint_limit_rejects_response_before_caching(self):
        from tasktrack.db import Error

        async def fetch(_client, path, body=None):
            return {"large": "x" * 200}, {}

        cache = {}
        with (
            patch("tasktrack.integrations.Client.get", fetch),
            patch("tasktrack.import_fetches.CHECKPOINT_LIMIT", 100),
        ):
            client = CheckpointClient(
                {"provider": "trello", "accounts": '[{"id":"a"}]'},
                {"access_token": "test"},
                "a",
                cache,
                None,
                "test",
            )
            with self.assertRaises(Error) as raised:
                await client.get("/large")
        self.assertEqual(raised.exception.payload["error"]["code"], "snapshot_limit")
        self.assertEqual(cache, {})

    async def test_durable_route_resumes_and_enforces_owner_and_cancel(self):
        import json
        import sys
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from tasktrack import import_fetches
        from tasktrack.db import Error
        from tests.test_agent_auth import AgentTests

        AgentTests.setUp(self)
        self.identity["csrf"] = "test-csrf"
        request = SimpleNamespace(headers={"X-CSRF-Token": "test-csrf"})
        files = {}

        class Response:
            @classmethod
            def json(cls, data, status=200):
                result = cls()
                result.data = data
                return result

            async def text(self):
                return json.dumps(self.data)

        class Bucket:
            async def put(self, key, value):
                files[key] = value

            async def get(self, key):
                if key not in files:
                    return None

                async def text():
                    return files[key]

                return SimpleNamespace(text=text)

            async def delete(self, key):
                files.pop(key, None)

        worker = SimpleNamespace(
            env=SimpleNamespace(FILES=Bucket()), task_call=AsyncMock()
        )
        row = {"id": "connection", "provider": "trello", "accounts": '[{"id":"a"}]'}
        descriptor = {
            "connection_id": "connection",
            "account_id": "a",
            "project_id": 1,
            "source_project": "b",
            "mapping": {},
        }
        body = AsyncMock(return_value=descriptor)
        calls = []

        async def provider_get(_client, path, body=None):
            calls.append(path)
            return {"id": path}, {}

        async def snapshot(*args, fetch_client, fetch_id, **kwargs):
            for i in range(15):
                await fetch_client.get("/" + str(i))
            return Response.json({"id": "preview", "fetch_id": fetch_id})

        fake_routes = SimpleNamespace(route=snapshot)
        with (
            patch.dict(sys.modules, {"workers": SimpleNamespace(Response=Response)}),
            patch("tasktrack.import_routes", fake_routes, create=True),
            patch.dict(sys.modules, {"tasktrack.import_routes": fake_routes}),
            patch(
                "tasktrack.integrations.connection",
                AsyncMock(return_value=(row, {"access_token": "test"})),
            ),
            patch("tasktrack.integrations.Client.get", provider_get),
        ):

            async def call(path, identity=None):
                return await import_fetches.route(
                    worker,
                    request,
                    self.accounts,
                    identity or self.identity,
                    path,
                    "POST",
                    body,
                    None,
                )

            started = await call("/api/v1/import-fetches")
            path = "/api/v1/import-fetches/" + started.data["id"]
            pending = await call(path + "/resume")
            self.assertEqual(pending.data["state"], "pending")
            self.assertEqual(len(calls), 12)
            with self.assertRaises(Error):
                await call(path + "/resume", self.identity | {"user_id": "other"})
            complete = await call(path + "/resume")
            self.assertEqual(complete.data["state"], "complete")
            self.assertEqual(len(calls), 15)
            self.assertEqual(len(files), 1)
            replay = await call(path + "/resume")
            self.assertEqual(replay.data["result"]["id"], "preview")
            self.assertEqual(len(calls), 15)
            cancelled = await call("/api/v1/import-fetches")
            cancel_path = "/api/v1/import-fetches/" + cancelled.data["id"]
            await call(cancel_path + "/cancel")
            with self.assertRaises(Error):
                await call(cancel_path + "/resume")
