CREATE TABLE passkeys (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 public_key TEXT NOT NULL,
 counter INTEGER NOT NULL,
 name TEXT NOT NULL,
 created_at INTEGER NOT NULL,
 last_used_at INTEGER,
 backed_up INTEGER NOT NULL,
 device_type TEXT NOT NULL
);
CREATE INDEX passkeys_user ON passkeys(user_id);
CREATE TABLE passkey_challenges (
 id_hash TEXT PRIMARY KEY,
 challenge TEXT NOT NULL,
 kind TEXT NOT NULL,
 user_id TEXT,
 session_hash TEXT,
 next_path TEXT NOT NULL,
 expires_at INTEGER NOT NULL
);
CREATE INDEX passkey_challenges_expiry ON passkey_challenges(expires_at);
CREATE TABLE passkey_sessions (
 session_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,
 credential_id TEXT NOT NULL REFERENCES passkeys(id) ON DELETE CASCADE
);
CREATE INDEX passkey_sessions_credential ON passkey_sessions(credential_id);
