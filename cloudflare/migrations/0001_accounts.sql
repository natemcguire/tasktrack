CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at INTEGER NOT NULL
);
CREATE TABLE memberships (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL CHECK(role IN ('owner','member')),
    created_at INTEGER NOT NULL,
    PRIMARY KEY(workspace_id,user_id)
);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    csrf TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);
CREATE TABLE login_links (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    next_path TEXT NOT NULL DEFAULT '/',
    expires_at INTEGER NOT NULL
);
CREATE TABLE api_tokens (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL REFERENCES users(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX api_tokens_workspace ON api_tokens(workspace_id);
CREATE TABLE invites (
    token_hash TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_by TEXT REFERENCES users(id)
);
CREATE TABLE shares (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    task_id INTEGER NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at INTEGER NOT NULL,
    revoked_at INTEGER,
    summary TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    preview_key TEXT NOT NULL,
    request_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    UNIQUE(workspace_id,created_by,request_key)
);
CREATE INDEX shares_task ON shares(workspace_id,task_id);
CREATE TABLE rate_limits (
    key TEXT PRIMARY KEY,
    window INTEGER NOT NULL,
    count INTEGER NOT NULL
);
-- Used only with a loopback PUBLIC_URL and LOCAL_EMAIL=1 during local checks.
CREATE TABLE local_mail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
