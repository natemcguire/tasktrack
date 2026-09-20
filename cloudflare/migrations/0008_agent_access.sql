CREATE TABLE agent_enrollments (
 id TEXT PRIMARY KEY, device_hash TEXT NOT NULL UNIQUE, code_hash TEXT NOT NULL UNIQUE,
 workspace_id TEXT NOT NULL, name TEXT NOT NULL, projects TEXT NOT NULL, capabilities TEXT NOT NULL,
 owner_id TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL, last_poll INTEGER NOT NULL DEFAULT 0, poll_interval INTEGER NOT NULL DEFAULT 5,
 grant_id TEXT
);
CREATE INDEX agent_enrollment_expiry ON agent_enrollments(expires_at);
CREATE TABLE agent_grants (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 name TEXT NOT NULL, projects TEXT NOT NULL, capabilities TEXT NOT NULL, created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL, revoked_at INTEGER, last_used_at INTEGER,
 refresh_hash TEXT UNIQUE, previous_refresh_hash TEXT, generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE agent_token_scopes (
 token_id TEXT PRIMARY KEY REFERENCES api_tokens(id) ON DELETE CASCADE,
 grant_id TEXT NOT NULL REFERENCES agent_grants(id)
);
CREATE TABLE agent_refresh_history (
 token_hash TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES agent_grants(id), used_at INTEGER NOT NULL
);
CREATE TABLE human_challenges (
 id_hash TEXT PRIMARY KEY, challenge TEXT NOT NULL, user_id TEXT NOT NULL,
 session_hash TEXT NOT NULL, binding TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE agent_security_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, user_id TEXT,
 subject_id TEXT NOT NULL, operation TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE agent_notification_preferences (
 user_id TEXT PRIMARY KEY REFERENCES users(id), email_enabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE agent_notifications (
 id TEXT PRIMARY KEY, enrollment_id TEXT NOT NULL UNIQUE REFERENCES agent_enrollments(id),
 user_id TEXT NOT NULL REFERENCES users(id), status TEXT NOT NULL DEFAULT 'queued',
 created_at INTEGER NOT NULL, sent_at INTEGER
);
CREATE TRIGGER agent_membership_update AFTER UPDATE OF kind,access_role,status,customer_id,capabilities ON memberships
BEGIN
 UPDATE agent_grants SET revoked_at=unixepoch(),refresh_hash=NULL WHERE user_id=OLD.user_id AND workspace_id=OLD.workspace_id;
 UPDATE agent_enrollments SET status='cancelled' WHERE owner_id=OLD.user_id AND workspace_id=OLD.workspace_id AND status IN ('pending','approved');
END;
CREATE TRIGGER agent_membership_delete AFTER DELETE ON memberships
BEGIN
 UPDATE agent_grants SET revoked_at=unixepoch(),refresh_hash=NULL WHERE user_id=OLD.user_id AND workspace_id=OLD.workspace_id;
 UPDATE agent_enrollments SET status='cancelled' WHERE owner_id=OLD.user_id AND workspace_id=OLD.workspace_id AND status IN ('pending','approved');
END;
