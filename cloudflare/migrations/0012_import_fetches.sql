CREATE TABLE import_fetches (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
 connection_id TEXT NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 cache_key TEXT, lease TEXT, lease_expires INTEGER NOT NULL DEFAULT 0,
 requests_done INTEGER NOT NULL DEFAULT 0, result TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX import_fetches_owner ON import_fetches(workspace_id,user_id,created_at);
