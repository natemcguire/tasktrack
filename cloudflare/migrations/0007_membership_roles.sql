-- Preserve membership identities and legacy role column during the v2 rollout.
ALTER TABLE memberships ADD COLUMN id TEXT;
ALTER TABLE memberships ADD COLUMN kind TEXT NOT NULL DEFAULT 'internal' CHECK(kind IN ('internal','external'));
ALTER TABLE memberships ADD COLUMN access_role TEXT NOT NULL DEFAULT 'regular' CHECK(access_role IN ('admin','billing','regular'));
ALTER TABLE memberships ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended'));
ALTER TABLE memberships ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE memberships ADD COLUMN customer_id TEXT;
ALTER TABLE memberships ADD COLUMN capabilities TEXT NOT NULL DEFAULT '[]';
UPDATE memberships SET id=lower(hex(randomblob(16))), access_role=CASE role WHEN 'owner' THEN 'admin' ELSE 'regular' END;
CREATE UNIQUE INDEX membership_identity ON memberships(id);
ALTER TABLE workspaces ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC';
CREATE TABLE membership_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL,
 membership_id TEXT NOT NULL, changed_by TEXT NOT NULL,
 before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TRIGGER membership_identity_insert AFTER INSERT ON memberships
BEGIN
 UPDATE memberships SET id=COALESCE(NEW.id,lower(hex(randomblob(16)))),
 access_role=(CASE WHEN NEW.role='owner' THEN 'admin' ELSE NEW.access_role END)
 WHERE workspace_id=NEW.workspace_id AND user_id=NEW.user_id;
END;
CREATE TRIGGER membership_external_insert BEFORE INSERT ON memberships
WHEN NEW.kind='external' AND (NEW.access_role!='regular' OR NEW.customer_id IS NULL OR NEW.role='owner')
BEGIN SELECT RAISE(ABORT,'invalid_external_membership'); END;
CREATE TRIGGER membership_external_update BEFORE UPDATE ON memberships
WHEN NEW.kind='external' AND (NEW.access_role!='regular' OR NEW.customer_id IS NULL OR NEW.role='owner')
BEGIN SELECT RAISE(ABORT,'invalid_external_membership'); END;
CREATE TRIGGER membership_last_admin_update BEFORE UPDATE ON memberships
WHEN OLD.kind='internal' AND OLD.access_role='admin' AND OLD.status='active'
 AND (NEW.kind!='internal' OR NEW.access_role!='admin' OR NEW.status!='active')
 AND NOT EXISTS(SELECT 1 FROM memberships WHERE workspace_id=OLD.workspace_id AND id!=OLD.id AND kind='internal' AND access_role='admin' AND status='active')
BEGIN SELECT RAISE(ABORT,'last_admin'); END;
CREATE TRIGGER membership_last_admin_delete BEFORE DELETE ON memberships
WHEN OLD.kind='internal' AND OLD.access_role='admin' AND OLD.status='active'
 AND NOT EXISTS(SELECT 1 FROM memberships WHERE workspace_id=OLD.workspace_id AND id!=OLD.id AND kind='internal' AND access_role='admin' AND status='active')
BEGIN SELECT RAISE(ABORT,'last_admin'); END;
CREATE TRIGGER membership_revoke_update AFTER UPDATE OF kind,access_role,status,customer_id,capabilities ON memberships
WHEN OLD.kind!=NEW.kind OR OLD.access_role!=NEW.access_role OR OLD.status!=NEW.status OR OLD.customer_id IS NOT NEW.customer_id OR OLD.capabilities!=NEW.capabilities
BEGIN
 DELETE FROM sessions WHERE workspace_id=OLD.workspace_id AND user_id=OLD.user_id;
 DELETE FROM api_tokens WHERE workspace_id=OLD.workspace_id AND user_id=OLD.user_id;
END;
CREATE TRIGGER membership_revoke_delete AFTER DELETE ON memberships
BEGIN
 DELETE FROM sessions WHERE workspace_id=OLD.workspace_id AND user_id=OLD.user_id;
 DELETE FROM api_tokens WHERE workspace_id=OLD.workspace_id AND user_id=OLD.user_id;
END;
CREATE TABLE tenant_creation_requests (
 user_id TEXT NOT NULL REFERENCES users(id), request_key TEXT NOT NULL,
 workspace_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
 PRIMARY KEY(user_id,request_key)
);
