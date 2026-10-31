-- An approval password is a second way to confirm agent approvals and other
-- sensitive changes when the browser has no passkey for the account (for example
-- under SSO, where sign-in happens at the hub and no Tasktrack passkey exists yet).
-- It never signs anyone in. PBKDF2-HMAC-SHA512, per-user salt, iteration count stored.
CREATE TABLE approval_passwords (
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 salt TEXT NOT NULL,
 hash TEXT NOT NULL,
 iterations INTEGER NOT NULL,
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL
);
