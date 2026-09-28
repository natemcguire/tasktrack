CREATE TABLE agent_channels (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), channel TEXT NOT NULL,
 recipient TEXT NOT NULL, verified_at INTEGER, code_hash TEXT, expires_at INTEGER NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 0,
 UNIQUE(user_id,channel)
);
CREATE TABLE security_deliveries (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), workspace_id TEXT NOT NULL,
 event_key TEXT NOT NULL, channel TEXT NOT NULL, recipient TEXT NOT NULL, body TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
 lease_hash TEXT, lease_expires INTEGER, sent_at INTEGER,
 UNIQUE(user_id,event_key,channel)
);
CREATE INDEX security_delivery_queue ON security_deliveries(user_id,workspace_id,status,channel);
