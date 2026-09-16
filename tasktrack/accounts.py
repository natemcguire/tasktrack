"""Hosted account and workspace authorization, backed by Cloudflare D1."""

import hashlib
import re
import secrets
import time
import uuid
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from .db import Error

SESSION_AGE = 30 * 24 * 60 * 60


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def timestamp():
    return int(time.time())


def token():
    return secrets.token_urlsafe(32)


def safe_next(value):
    if not isinstance(value, str):
        return "/"
    return (
        value
        if re.fullmatch(
            r"/(?:preview/return/[a-f0-9-]{36}/[A-Za-z0-9_-]{43}|account|triage|invite/[A-Za-z0-9_-]{43}|tasks/[0-9]+|projects/[A-Za-z0-9]+(?:/settings)?)?",
            value or "",
        )
        else "/"
    )


def cookie_value(request, name):
    cookie = SimpleCookie()
    try:
        cookie.load(request.headers.get("Cookie", ""))
        return cookie[name].value if name in cookie else ""
    except Exception:
        return ""


class Accounts:
    def __init__(self, env):
        self.env = env
        self.db = env.ACCOUNTS
        self.origin = env.PUBLIC_URL.rstrip("/")
        self.local = urlsplit(self.origin).hostname in {"localhost", "127.0.0.1"}
        self.cookie_name = "tt_session" if self.local else "__Host-tt_session"

    def statement(self, sql, *args):
        return self.db.prepare(sql).bind(*args)

    async def one(self, sql, *args):
        return await self.statement(sql, *args).first()

    async def many(self, sql, *args):
        return (await self.statement(sql, *args).all())["results"]

    async def run(self, sql, *args):
        return await self.statement(sql, *args).run()

    async def limit(self, key, maximum, seconds=600):
        window = timestamp() // seconds
        row = await self.one(
            "INSERT INTO rate_limits(key,window,count,updated_at) VALUES (?,?,1,?) "
            "ON CONFLICT(key) DO UPDATE SET window=excluded.window, updated_at=excluded.updated_at, "
            "count=CASE WHEN rate_limits.window=excluded.window THEN rate_limits.count+1 ELSE 1 END "
            "RETURNING count",
            digest(key),
            window,
            timestamp(),
        )
        if row["count"] > maximum:
            raise Error(
                429, "rate_limited", "Please wait a few minutes before trying again."
            )

    async def login_link(self, email, next_path, ip):
        await self.limit("login-ip:" + ip, 10)
        if not isinstance(email, str):
            raise Error(422, "email", "Enter your email address.")
        email = email.strip().lower()
        if len(email) > 254 or not re.fullmatch(
            r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email
        ):
            raise Error(422, "email", "Enter a valid email address.")
        await self.limit("login-email:" + email, 3)
        secret, challenge = token(), token()
        code = f"{secrets.randbelow(1000000):06d}"
        await self.run(
            "INSERT INTO login_links VALUES (?,?,?,?)",
            digest(secret),
            email,
            safe_next(next_path),
            timestamp() + 15 * 60,
        )
        await self.run(
            "INSERT INTO login_codes VALUES (?,?,?,0,?)",
            digest(challenge),
            digest(secret),
            digest(challenge + ":" + code),
            timestamp() + 900,
        )
        link = self.origin + "/auth/verify?token=" + secret
        body = (
            "Your Tasktrack sign-in code: "
            + code
            + "\n\nIf the code does not work, use this link:\n\n"
            + link
            + "\n\nThis link expires in 15 minutes and works once. If you did not request it, you can ignore this email."
        )
        if self.local and getattr(self.env, "LOCAL_EMAIL", "") == "1":
            await self.run(
                "INSERT INTO local_mail(recipient,body,created_at) VALUES (?,?,?)",
                email,
                body,
                timestamp(),
            )
            return challenge
        try:
            await self.env.EMAIL.send(
                {
                    "from": {
                        "email": getattr(
                            self.env,
                            "MAIL_FROM",
                            "hello@" + urlsplit(self.origin).hostname,
                        ),
                        "name": "Tasktrack",
                    },
                    "to": email,
                    "subject": code + " is your Tasktrack sign-in code",
                    "text": body,
                }
            )
        except Exception:
            await self.run("DELETE FROM login_links WHERE token_hash=?", digest(secret))
            raise Error(
                503,
                "email_unavailable",
                "We could not send your sign-in link. Please try again shortly.",
            ) from None

        return challenge

    async def redeem_code(self, challenge, code, ip):
        await self.limit("code-ip:" + ip, 30)
        if not isinstance(challenge, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{43}", challenge
        ):
            raise Error(400, "invalid_code", "Request a new sign-in code.")
        row = await self.one(
            "UPDATE login_codes SET attempts=attempts+1 WHERE challenge_hash=? AND attempts<5 AND expires_at>? RETURNING link_hash,code_hash",
            digest(challenge),
            timestamp(),
        )
        if (
            not row
            or not isinstance(code, str)
            or not secrets.compare_digest(
                row["code_hash"], digest(challenge + ":" + code)
            )
        ):
            raise Error(
                400,
                "invalid_code",
                "That code is incorrect, expired or already used. Try again or request a new code.",
            )
        return await self.redeem_hash(row["link_hash"])

    async def redeem(self, secret, ip):
        await self.limit("redeem:" + ip, 30)
        if not isinstance(secret, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{43}", secret
        ):
            raise Error(
                400, "invalid_link", "This sign-in link is invalid. Request a new one."
            )
        return await self.redeem_hash(digest(secret))

    async def redeem_hash(self, link_hash):
        # DELETE ... RETURNING makes the link single-use across concurrent requests.
        link = await self.one(
            "DELETE FROM login_links WHERE token_hash=? AND expires_at>? RETURNING email,next_path",
            link_hash,
            timestamp(),
        )
        if not link:
            raise Error(
                400,
                "expired_link",
                "This link has expired or was already used. Request a new one.",
            )
        uid, wid, session, csrf, now = (
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            token(),
            token(),
            timestamp(),
        )
        name = link["email"].split("@")[0][:80]
        await self.db.batch(
            [
                self.statement(
                    "INSERT OR IGNORE INTO users VALUES (?,?,?,?)",
                    uid,
                    link["email"],
                    name,
                    now,
                ),
                self.statement(
                    "INSERT INTO workspaces(id,name,created_by,created_at) SELECT ?,?,id,? FROM users WHERE id=?",
                    wid,
                    name + "’s workspace",
                    now,
                    uid,
                ),
                self.statement(
                    "INSERT INTO memberships(workspace_id,user_id,role,created_at) SELECT ?,?,'owner',? WHERE EXISTS(SELECT 1 FROM workspaces WHERE id=?)",
                    wid,
                    uid,
                    now,
                    wid,
                ),
                self.statement(
                    "INSERT INTO sessions SELECT ?,u.id,(SELECT workspace_id FROM memberships WHERE user_id=u.id ORDER BY created_at,workspace_id LIMIT 1),?,? FROM users u WHERE email=?",
                    digest(session),
                    csrf,
                    now + SESSION_AGE,
                    link["email"],
                ),
            ]
        )
        return session, safe_next(link["next_path"])

    def cookie(self, session, clear=False):
        return (
            f"{self.cookie_name}={session}; Path=/; HttpOnly; SameSite=Lax; Max-Age={0 if clear else SESSION_AGE}"
            + ("" if self.local else "; Secure")
        )

    async def authenticate(self, request, preview_secret=None):
        authorization = request.headers.get("Authorization", "")
        bearer = authorization.startswith("Bearer ")
        secret = (
            authorization[7:] if bearer else cookie_value(request, self.cookie_name)
        )
        if preview_secret is not None:
            secret = preview_secret
            bearer = False
        if not secret or len(secret) > 300:
            return None
        session_hash = digest(secret)
        if preview_secret is not None:
            preview = await self.one(
                "SELECT session_hash FROM preview_sessions WHERE token_hash=? AND expires_at>?",
                session_hash,
                timestamp(),
            )
            if not preview:
                return None
            session_hash = preview["session_hash"]
        table = "api_tokens" if bearer else "sessions"
        extra = "s.actor,s.id AS token_id" if bearer else "s.csrf,u.email AS actor"
        identity = await self.one(
            f"SELECT u.id AS user_id,u.email,u.name,s.workspace_id,w.name AS workspace_name,m.role,{extra} "
            f"FROM {table} s JOIN users u ON u.id=s.user_id JOIN workspaces w ON w.id=s.workspace_id "
            "JOIN memberships m ON m.user_id=s.user_id AND m.workspace_id=s.workspace_id "
            "WHERE s.token_hash=? AND s.expires_at>?",
            session_hash,
            timestamp(),
        )
        if identity:
            identity.update(bearer=bearer, session_hash=session_hash)
        return identity

    def csrf(self, request, identity, body=None):
        if identity["bearer"]:
            return
        supplied = request.headers.get("X-CSRF-Token") or (body or {}).get("csrf", "")
        if not isinstance(supplied, str) or not secrets.compare_digest(
            supplied, identity["csrf"]
        ):
            raise Error(403, "csrf", "This page has expired. Reload and try again.")

    def owner(self, identity):
        if identity["role"] != "owner":
            raise Error(403, "owner_required", "Only the workspace owner can do this.")

    async def overview(self, identity):
        wid = identity["workspace_id"]
        return {
            "passkeys": await self.many("SELECT id,name,created_at,last_used_at FROM passkeys WHERE user_id=? ORDER BY created_at",identity["user_id"]),
            "workspaces": await self.many(
                "SELECT w.id,w.name,m.role FROM memberships m JOIN workspaces w ON w.id=m.workspace_id WHERE m.user_id=? ORDER BY w.created_at,w.id",
                identity["user_id"],
            ),
            "members": await self.many(
                "SELECT u.id,u.name,u.email,m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? ORDER BY m.created_at",
                wid,
            ),
            "tokens": await self.many(
                "SELECT id,name,actor,created_at,expires_at FROM api_tokens WHERE workspace_id=? AND user_id=? ORDER BY created_at DESC",
                wid,
                identity["user_id"],
            ),
            "invites": await self.many(
                "SELECT token_hash,created_at,expires_at,used_by FROM invites WHERE workspace_id=? AND expires_at>? ORDER BY created_at DESC",
                wid,
                timestamp(),
            ),
        }

    async def switch(self, identity, workspace_id):
        row = await self.one(
            "SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?",
            workspace_id,
            identity["user_id"],
        )
        if not row:
            raise Error(404, "workspace_missing", "Workspace not found.")
        # An old tab must not write into the newly selected workspace.
        await self.run(
            "UPDATE sessions SET workspace_id=?,csrf=? WHERE token_hash=?",
            workspace_id,
            token(),
            identity["session_hash"],
        )

    async def remove_member(self, identity, user_id):
        self.owner(identity)
        wid = identity["workspace_id"]
        member = await self.one(
            "SELECT role FROM memberships WHERE workspace_id=? AND user_id=?",
            wid,
            user_id,
        )
        if not member or member["role"] == "owner":
            raise Error(404, "member_missing", "This member cannot be removed.")
        await self.db.batch(
            [
                self.statement(
                    "DELETE FROM memberships WHERE workspace_id=? AND user_id=? AND role='member'",
                    wid,
                    user_id,
                ),
                self.statement(
                    "DELETE FROM api_tokens WHERE workspace_id=? AND user_id=?",
                    wid,
                    user_id,
                ),
                self.statement(
                    "DELETE FROM sessions WHERE workspace_id=? AND user_id=?",
                    wid,
                    user_id,
                ),
            ]
        )

    async def invite(self, identity):
        self.owner(identity)
        await self.limit("invite:" + identity["user_id"], 10, 3600)
        secret = token()
        await self.run(
            "INSERT INTO invites VALUES (?,?,?,?,?,NULL)",
            digest(secret),
            identity["workspace_id"],
            identity["user_id"],
            timestamp(),
            timestamp() + 48 * 3600,
        )
        return self.origin + "/invite/" + secret

    async def join(self, identity, secret):
        found = await self.one(
            "SELECT workspace_id FROM invites WHERE token_hash=? AND expires_at>? AND used_by IS NULL",
            digest(secret),
            timestamp(),
        )
        if not found:
            raise Error(
                404,
                "invite_expired",
                "This invitation has expired or was already used.",
            )
        wid = found["workspace_id"]
        # The membership INSERT is conditional on this request winning the invite.
        results = await self.db.batch(
            [
                self.statement(
                    "UPDATE invites SET used_by=? WHERE token_hash=? AND used_by IS NULL AND expires_at>? RETURNING workspace_id",
                    identity["user_id"],
                    digest(secret),
                    timestamp(),
                ),
                self.statement(
                    "INSERT OR IGNORE INTO memberships(workspace_id,user_id,role,created_at) SELECT workspace_id,?,'member',? FROM invites WHERE token_hash=? AND used_by=?",
                    identity["user_id"],
                    timestamp(),
                    digest(secret),
                    identity["user_id"],
                ),
            ]
        )
        if not results[0]["results"]:
            raise Error(409, "invite_used", "This invitation was already used.")
        await self.switch(identity, wid)

    async def create_token(self, identity, name):
        self.owner(identity)
        if not isinstance(name, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9 _.-]{0,59}", name.strip()
        ):
            raise Error(
                422,
                "token_name",
                "Use a short agent name with letters, numbers, spaces, dots or hyphens.",
            )
        await self.limit("token:" + identity["user_id"], 10, 3600)
        secret = "tt_" + token()
        await self.run(
            "INSERT INTO api_tokens VALUES (?,?,?,?,?,?,?,?)",
            str(uuid.uuid4()),
            digest(secret),
            identity["user_id"],
            identity["workspace_id"],
            name.strip(),
            "agent:" + name.strip(),
            timestamp(),
            timestamp() + 90 * 86400,
        )
        return secret
