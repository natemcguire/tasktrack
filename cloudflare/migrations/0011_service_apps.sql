CREATE TABLE service_apps (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), name TEXT NOT NULL,
 projects TEXT NOT NULL, capabilities TEXT NOT NULL, secret_hash TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL, created_at INTEGER NOT NULL,
 rotated_at INTEGER, revoked_at INTEGER, last_used_at INTEGER
);
CREATE INDEX service_apps_workspace ON service_apps(workspace_id);
CREATE TABLE service_app_tokens (
 token_hash TEXT PRIMARY KEY, app_id TEXT NOT NULL REFERENCES service_apps(id),
 revision INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX service_app_token_expiry ON service_app_tokens(expires_at);
CREATE TABLE service_app_events (
 id INTEGER PRIMARY KEY, app_id TEXT NOT NULL, actor TEXT NOT NULL, operation TEXT NOT NULL, created_at INTEGER NOT NULL
);
