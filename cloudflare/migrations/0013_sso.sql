CREATE TABLE sso_sessions (
    session_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,
    hub_token TEXT NOT NULL
);
CREATE TABLE sso_states (
    state_hash TEXT PRIMARY KEY,
    next_path TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
