CREATE TABLE login_codes (
    challenge_hash TEXT PRIMARY KEY,
    link_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at INTEGER NOT NULL
);
CREATE INDEX login_codes_expiry ON login_codes(expires_at);
