import json
import unittest

from tasktrack.db import Error
from tasktrack.imports import validate
from tasktrack.integrations import Client
from tasktrack.providers import normalize


class ProviderTests(unittest.TestCase):
    def test_jira_incomplete_pages_and_original_author(self):
        b = normalize(
            "jira",
            {
                "project": {"id": "1"},
                "issues": [
                    {
                        "key": "A-1",
                        "fields": {
                            "summary": "Task",
                            "status": {
                                "name": "Done",
                                "statusCategory": {"key": "done"},
                            },
                            "comment": {
                                "total": 2,
                                "comments": [
                                    {
                                        "id": "1",
                                        "body": {
                                            "type": "doc",
                                            "content": [
                                                {
                                                    "type": "paragraph",
                                                    "content": [
                                                        {
                                                            "type": "text",
                                                            "text": "hello",
                                                        }
                                                    ],
                                                }
                                            ],
                                        },
                                        "author": {"displayName": "Alex"},
                                    }
                                ],
                            },
                        },
                    }
                ],
            },
        )
        self.assertEqual(b["records"][0]["comments"][0]["author"], "Alex")
        self.assertEqual(b["records"][0]["phase"], "done")
        self.assertTrue(any("incomplete" in w for w in b["warnings"]))
        validate(b)

    def test_trello_export_discloses_action_limit_and_preserves_checklists(self):
        b = normalize(
            "trello",
            {
                "id": "b",
                "lists": [{"id": "l", "name": "Done"}],
                "cards": [{"id": "c", "idList": "l", "name": "Card"}],
                "checklists": [
                    {
                        "idCard": "c",
                        "name": "Checks",
                        "checkItems": [{"name": "One", "state": "complete"}],
                    }
                ],
            },
            mapping={"Done": "done"},
        )
        self.assertTrue(any("1,000" in w for w in b["warnings"]))
        self.assertIn("- [x] One", b["records"][0]["description"])
        self.assertEqual(b["records"][0]["phase"], "done")

    def test_basecamp_unknown_content_is_counted(self):
        b = normalize(
            "basecamp",
            {
                "id": 1,
                "recordings": [
                    {"id": 2, "type": "Message", "title": "Keep me"},
                    {"id": 4, "type": "Door", "title": "External link"},
                    {"id": 3, "type": "Todo", "title": "Work", "completed": True},
                ],
            },
        )
        self.assertEqual(len(b["records"]), 2)
        self.assertTrue(any("Door" in w for w in b["warnings"]))

    def test_csv_requires_mapping_and_stable_ids(self):
        raw = "id,title,status\n1,Work,Doing\n"
        with self.assertRaises(Error):
            normalize("kanban", raw)
        b = validate(normalize("kanban", raw, mapping={"Doing": "in_progress"}))
        self.assertEqual(b["records"][0]["phase"], "in_progress")


class ProviderPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_trello_comments_beyond_export_action_limit(self):
        client = Client(
            {"provider": "trello", "accounts": json.dumps([{"id": "a"}])},
            {"access_token": "test"},
            "a",
        )
        calls = []

        async def get(path, body=None):
            calls.append(path)
            if "/actions?" in path:
                return (
                    [
                        {
                            "id": str(i),
                            "type": "commentCard",
                            "data": {"card": {"id": "c"}, "text": "Comment " + str(i)},
                        }
                        for i in range(1000)
                    ]
                    if "before=" not in path
                    else [
                        {
                            "id": "1001",
                            "type": "commentCard",
                            "data": {"card": {"id": "c"}, "text": "Last comment"},
                        }
                    ]
                ), {}
            if path.endswith("?fields=dateLastActivity"):
                return {"dateLastActivity": "2026-01-01T00:00:00Z"}, {}
            return {
                "id": "b",
                "dateLastActivity": "2026-01-01T00:00:00Z",
                "lists": [{"id": "l", "name": "Todo"}],
                "cards": [{"id": "c", "idList": "l", "name": "Card"}],
            }, {}

        client.get = get
        snapshot = await client.snapshot("b")
        result = normalize("trello", snapshot, "a", {"Todo": "backlog"})
        self.assertEqual(len(result["records"][0]["comments"]), 1001)
        self.assertTrue(any("before=999" in p for p in calls))
        self.assertTrue(result["complete"])

    async def test_basecamp_link_pagination_does_not_drop_pages(self):
        client = Client(
            {"provider": "basecamp", "accounts": json.dumps([{"id": "1"}])},
            {"access_token": "test"},
            "1",
        )
        seen = []

        async def get(path, body=None):
            seen.append(path)
            return (
                (
                    [{"id": 1}],
                    {
                        "link": '<https://3.basecampapi.com/1/projects.json?page=2>; rel="next"'
                    },
                )
                if len(seen) == 1
                else ([{"id": 2}], {})
            )

        client.get = get
        result = await client.pages("/projects.json")
        self.assertEqual([r["id"] for r in result], [1, 2])
        self.assertFalse(client.allowed("https://evil.example/1/projects.json"))
        self.assertFalse(client.allowed("https://3.basecampapi.com/12/projects.json"))
        self.assertFalse(
            client.allowed("https://3.basecampapi.com/1/%2e%2e/2/projects.json")
        )
        self.assertFalse(
            client.allowed("https://3.basecampapi.com:444/1/projects.json")
        )
