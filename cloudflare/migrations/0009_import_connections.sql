CREATE TABLE import_oauth_states (
 state_hash TEXT PRIMARY KEY, provider TEXT NOT NULL, user_id TEXT NOT NULL,
 workspace_id TEXT NOT NULL, session_hash TEXT NOT NULL, verifier TEXT NOT NULL,
 expires_at INTEGER NOT NULL
);
CREATE TABLE import_connections (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, user_id TEXT NOT NULL,
 workspace_id TEXT NOT NULL, secret TEXT NOT NULL, accounts TEXT NOT NULL,
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
 revoked_at INTEGER, refresh_lock INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX import_connections_owner ON import_connections(workspace_id,user_id);
CREATE TRIGGER import_membership_revoke AFTER UPDATE OF kind,access_role,status,capabilities ON memberships
BEGIN
 UPDATE import_connections SET revoked_at=unixepoch(),secret='' WHERE workspace_id=OLD.workspace_id AND user_id=OLD.user_id;
END;
CREATE TRIGGER import_membership_delete AFTER DELETE ON memberships
BEGIN
 UPDATE import_connections SET revoked_at=unixepoch(),secret='' WHERE workspace_id=OLD.workspace_id AND user_id=OLD.user_id;
END;
