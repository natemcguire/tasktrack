CREATE TABLE sso_sessions (
    session_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,
    hub_token TEXT NOT NULL
);
CREATE TABLE sso_states (
    state_hash TEXT PRIMARY KEY,
    next_path TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);

-- Preserve Nate's existing UUID, three workspaces, memberships and API tokens
-- when switching his verified login identity to the organization admin email.
UPDATE users SET email='nate.mcguire@gmail.com'
WHERE email='nate@eastbayprojects.com'
  AND NOT EXISTS (SELECT 1 FROM users WHERE email='nate.mcguire@gmail.com');
