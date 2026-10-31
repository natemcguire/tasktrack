"""Step-up confirmation with an approval password (the passkey-less path)."""

import unittest

from tasktrack import passkeys
from tasktrack.db import Error

from .test_agent_auth import AgentTests


class StepUpPasswordTests(AgentTests):
    def setUp(self):
        super().setUp()
        self.c.execute(
            "INSERT INTO sessions VALUES('sess','u','w','csrf',?)",
            (passkeys.timestamp() + passkeys.SESSION_AGE,),
        )
        self.identity = dict(self.identity, session_hash="sess", email="u@example.com")

    async def test_nothing_enrolled_blocks_step_up(self):
        with self.assertRaises(Error) as caught:
            await passkeys.step_up_options(self.accounts, self.identity, "b")
        self.assertEqual(caught.exception.status, 403)

    async def test_password_rules(self):
        for bad in ("short", " padded-start-12", "aaaaaaaaaaaaaa", "u@example.com", 7):
            with self.assertRaises(Error) as caught:
                await passkeys.set_password(self.accounts, self.identity, {"password": bad})
            self.assertEqual(caught.exception.status, 422, bad)
        saved = await passkeys.set_password(
            self.accounts, self.identity, {"password": "correct horse battery"}
        )
        self.assertTrue(saved["saved"])
        row = await passkeys.password_row(self.accounts, "u")
        self.assertNotIn("correct horse", row["hash"])
        self.assertEqual(row["iterations"], passkeys.PASSWORD_ITERATIONS)

    async def test_password_only_challenge_has_no_passkey_options(self):
        await passkeys.set_password(
            self.accounts, self.identity, {"password": "correct horse battery"}
        )
        challenge = await passkeys.step_up_options(self.accounts, self.identity, "b")
        self.assertEqual(challenge["methods"], ["password"])
        self.assertIsNone(challenge["options"])
        await passkeys.step_up_verify(
            self.accounts,
            self.identity,
            "b",
            {"challenge_id": challenge["challenge_id"], "password": "correct horse battery"},
        )
        # Spent: the same challenge cannot confirm twice.
        with self.assertRaises(Error) as caught:
            await passkeys.step_up_verify(
                self.accounts,
                self.identity,
                "b",
                {"challenge_id": challenge["challenge_id"], "password": "correct horse battery"},
            )
        self.assertEqual(caught.exception.payload["error"]["code"], "challenge_expired")

    async def test_wrong_password_binding_and_lockout(self):
        await passkeys.set_password(
            self.accounts, self.identity, {"password": "correct horse battery"}
        )
        challenge = await passkeys.step_up_options(self.accounts, self.identity, "b")
        # A challenge issued for one action cannot confirm another.
        with self.assertRaises(Error) as caught:
            await passkeys.step_up_verify(
                self.accounts,
                self.identity,
                "other",
                {"challenge_id": challenge["challenge_id"], "password": "correct horse battery"},
            )
        self.assertEqual(caught.exception.payload["error"]["code"], "challenge_expired")
        codes = []
        for _ in range(6):
            challenge = await passkeys.step_up_options(self.accounts, self.identity, "b")
            try:
                await passkeys.step_up_verify(
                    self.accounts,
                    self.identity,
                    "b",
                    {"challenge_id": challenge["challenge_id"], "password": "wrong wrong wrong"},
                )
            except Error as e:
                codes.append(e.payload["error"]["code"])
        self.assertEqual(codes, ["password_invalid"] * 5 + ["rate_limited"])
        # A passkey response is still accepted on the other branch: no password key means WebAuthn.
        challenge = await passkeys.step_up_options(self.accounts, self.identity, "b")
        with self.assertRaises(Error) as caught:
            await passkeys.step_up_verify(
                self.accounts,
                self.identity,
                "b",
                {"challenge_id": challenge["challenge_id"], "credential": {"id": "nope"}},
            )
        self.assertEqual(caught.exception.payload["error"]["code"], "passkey_invalid")

    async def test_remove_password(self):
        with self.assertRaises(Error):
            await passkeys.remove_password(self.accounts, self.identity)
        await passkeys.set_password(
            self.accounts, self.identity, {"password": "correct horse battery"}
        )
        self.assertEqual(
            await passkeys.remove_password(self.accounts, self.identity), {"removed": True}
        )
        with self.assertRaises(Error) as caught:
            await passkeys.step_up_options(self.accounts, self.identity, "b")
        self.assertEqual(caught.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
