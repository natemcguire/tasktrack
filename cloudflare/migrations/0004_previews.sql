-- Preview permissions are deliberately narrower than workspace membership.
CREATE TABLE preview_projects (
    workspace_id TEXT NOT NULL,
    project_id INTEGER NOT NULL,
    team_access INTEGER NOT NULL DEFAULT 0 CHECK(team_access IN (0,1)),
    PRIMARY KEY(workspace_id,project_id)
);
CREATE TABLE preview_members (
    workspace_id TEXT NOT NULL,
    project_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    PRIMARY KEY(workspace_id,project_id,email)
);
CREATE TABLE previews (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    object_key TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE TABLE preview_grants (
    token_hash TEXT PRIMARY KEY,
    challenge_hash TEXT NOT NULL,
    session_hash TEXT NOT NULL,
    preview_id TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE preview_sessions (
    token_hash TEXT PRIMARY KEY,
    session_hash TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
