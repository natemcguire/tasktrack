ALTER TABLE rate_limits ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0;
CREATE INDEX rate_limits_updated ON rate_limits(updated_at);
