# Provider imports

Implemented September 20, 2026. Open `/imports` from Account settings. Browser administrators can import Jira, Trello, Basecamp JSON exports or generic Kanban CSV/JSON into an existing project. Connected services become available after the deployment operator configures OAuth applications.

## Workflow

Choose a provider and export file or connection, select the source and destination project, map columns to workflow phases, and acknowledge destination access. Review record counts, missing attachments and coverage warnings before committing. Upload requested original files; SHA-256 and byte size must match the manifest. Download the reconciliation report and original source archive afterward.

Imports preserve original dates, descriptions, source identity, history, comments, verified attachments and supported epic/task hierarchy. Source authors are clearly labeled rather than impersonated. The preview lets administrators map source assignees to active internal workspace members with access to the destination project. Unmapped people remain in provenance and tasks stay unassigned. Multiple source assignees must resolve to at most one native assignee per record. Saving a mapping changes the preview digest and requires reviewing it again. Completed childless epics retain their historical state without fabricated workflow evidence. The board renders imported custom columns.

A job commits in batches of 25 records. Cancel stops future batches; resume continues durable checkpoints. Repeated source identities are deduplicated. Changed source records become conflicts and never overwrite later Tasktrack edits. Rollback requires a fresh human passkey approval and archives only unchanged job-created records; later edits, comments, files or dependencies block rollback.

## Coverage and limits

- Jira: issue pagination, full comment/history pagination, final source revision check, types, status, hierarchy and downloadable attachments.
- Trello: lists/cards, checklists, comments across action pages, attachments and archive state. Ordinary JSON exports may contain only the latest 1,000 actions; this is disclosed.
- Basecamp: to-dos, to-do lists, card records, comments, uploads, messages, documents, schedule entries and question answers. Non-task records become tasks with original record types and source metadata; provider-specific interactions are not reproduced. Unsupported tools, embedded files and unavailable history are reported as partial. This is not a full-fidelity Basecamp backup.
- Generic Kanban: CSV columns `id,title,status,phase` plus optional supported dates and parents, or JSON cards/normalized bundles. File exports without independent source inventories remain partial.

Limits: 2 MiB browser upload, 5,000 records per job, 500 KiB per record, 10 MiB per attachment and 50 destination columns. Connected source fetching checkpoints responses and downloaded bytes between requests (at most 12 new provider calls per resume). The browser resumes interrupted snapshots; checkpoints are valid for 24 hours and JSON responses are limited to 32 MiB. Native ingestion is independently resumable. Expired checkpoint objects currently require operator storage cleanup. Provider rate limits produce a retry message. Very large connected exports have not been production load-tested. Imports never invite users or delete source data.

## Provider configuration

Set Worker secrets `IMPORT_ENCRYPTION_KEY` (base64-encoded random 32 bytes), `JIRA_CLIENT_ID`, `JIRA_CLIENT_SECRET`, `TRELLO_CLIENT_ID`, `TRELLO_CLIENT_SECRET`, `BASECAMP_CLIENT_ID`, and `BASECAMP_CLIENT_SECRET` for the desired providers. Register exact callbacks `https://tasks.eastbayprojects.com/integrations/{provider}/callback`.

Jira uses OAuth 2.0 3LO with read scopes; Trello uses OAuth 2.0 with PKCE; Basecamp uses its web-server OAuth flow. Connections are bound to the human owner and workspace. Credentials are encrypted with AES-GCM and never returned to browsers or agents. Disconnect and membership revocation stop future fetches. Live provider consent and production import checks require configured applications; fixture tests do not establish those credentials work.

## API and CLI

All `/api/v1` routes require current workspace/project authority. Import operations require administrator access and `integrations.manage` for scoped agents.

| Route | Purpose |
| --- | --- |
| `POST /import-connections/{provider}/authorize` | Browser-owned OAuth connection |
| `GET /import-connections/{id}/projects` | Accessible source projects |
| `GET /import-connections/{id}/statuses` | Source columns |
| `POST /import-connections/{id}/snapshot` | Fetch, archive and prepare preview |
| `GET, POST /import-fetches` | List or start durable connected snapshots |
| `POST /import-fetches/{id}/resume` | Fetch the next bounded source chunk |
| `POST /import-fetches/{id}/cancel` | Stop further source fetching |
| `GET /import-people` | Eligible internal workspace members |
| `POST /import-jobs/{id}/people` | Map source person IDs to member user IDs before commit |
| `POST /import-analyze` | Inspect an export and mappings |
| `POST /import-previews` | Archive a file export and prepare job |
| `POST /import-jobs` | Prepare a normalized bundle |
| `GET /import-jobs/{id}` | Progress and reconciliation report |
| `POST /import-jobs/{id}/commit` | Commit next batch with preview digest |
| `POST /import-jobs/{id}/resume` | Continue an interrupted job |
| `POST /import-jobs/{id}/cancel` | Cancel uncommitted work |
| `POST /import-jobs/{id}/files/{sha256}` | Verify and stage original bytes |
| `GET /import-jobs/{id}/source` | Download authorized source archive |

`tt import --help` exposes normalize, preview, list, get, upload, commit, resume and cancel. File import adapters can be used without provider credentials.

Tests cover historical empty epics, privacy acknowledgement, duplicates, conflicts, hierarchy cycles, hashes, batches, cancellation, rollback conflicts, Trello history beyond 1,000 actions, pagination boundaries, SSRF denial and phone layout.
