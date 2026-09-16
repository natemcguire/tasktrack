CREATE TABLE client_previews (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
 task_id TEXT NOT NULL,
 version INTEGER NOT NULL,
 image TEXT NOT NULL,
 PRIMARY KEY(workspace_id, task_id)
);
