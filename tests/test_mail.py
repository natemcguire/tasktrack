import asyncio
import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from tasktrack import mail

MESSAGE = {
    "from": {"email": "hello@tasks.eastbayprojects.com", "name": "Tasktrack"},
    "to": "a@example.com",
    "subject": "s",
    "text": "t",
}


class Binding:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class Relay:
    def __init__(self, status):
        self.status = status
        self.calls = []

    async def request(self, payload):
        self.calls.append(json.loads(payload))
        return json.dumps({"status": self.status, "headers": {}, "body": ""})


class MailTest(unittest.TestCase):
    def test_binding_used_without_relay(self):
        email = Binding()
        asyncio.run(mail.send(SimpleNamespace(EMAIL=email), MESSAGE))
        self.assertEqual(email.sent, [MESSAGE])

    def run_relay(self, status):
        relay = Relay(status)
        env = SimpleNamespace(
            MAIL_RELAY_URL="https://relay.example/send", MAIL_RELAY_SECRET="s3cret"
        )
        fake = types.ModuleType("tasktrack.integrations")
        fake.module = lambda: relay
        with mock.patch.dict(sys.modules, {"tasktrack.integrations": fake}):
            asyncio.run(mail.send(env, MESSAGE))
        return relay

    def test_relay_posts_message_with_secret(self):
        call = self.run_relay(202).calls[0]
        self.assertEqual(call["url"], "https://relay.example/send")
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["headers"]["Authorization"], "Bearer s3cret")
        self.assertEqual(json.loads(call["body"]), MESSAGE)

    def test_relay_rejection_raises(self):
        with self.assertRaises(ValueError):
            self.run_relay(401)


if __name__ == "__main__":
    unittest.main()
