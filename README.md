# Tasktrack

## Give your agents a board they can come back to.

When an agent loses its session, the next one needs to know what was being built,
what's finished and where to start. Save that on the task.

Tasktrack gives a person and their independent coding agents three things:

1. **Requirements:** project and epic briefs, plus task acceptance criteria, kept with the work.
2. **Ownership:** an assignee and an explicit claim from the session doing the work.
3. **Handoffs:** progress, the next action and evidence that another session can pick up.

One Python service, a browser board, an HTTP API, a `tt` CLI and SQLite.
[MIT licensed](LICENSE).

![Tasktrack board with projects, epics and work across four columns](docs/screenshots/board.png)

## Get started

Python 3.11+. No extra runtime dependencies or frontend build.

```sh
git clone https://github.com/natemcguire/tasktrack.git
cd tasktrack
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
tt serve
```

Open **[localhost:7777](http://127.0.0.1:7777/)**. Enter your name in **Acting as**,
create a project and add a task.

Data lives in `~/.local/share/tasktrack`. Set `TT_DATA_DIR` to choose another
directory. The app runs locally, and the browser works offline.

## Put the requirements on the board

Use the project brief for the product requirements document (PRD). Break larger
work into epics, then give each task a description and acceptance criteria.
Comments, files and discussion links stay with the task.

Cards move through **Backlog → In progress → Review → Done**. Drag a card or use
its menu to move and reorder it. Filter by assignee, epic, priority or blockers.

The browser, API and CLI use the same records. To create work from a terminal:

```sh
tt project create HBR Harbor --as nate
tt task create HBR --file docs/example-task.json --as nate --json
```

The [example task](docs/example-task.json) is assigned to `codex@harbor` and
includes acceptance criteria. Use the returned task reference and version in
subsequent commands. The examples below use `HBR-1`.

## Claim, checkpoint and hand off

```sh
export TT_ACTOR=codex@harbor
export TT_SESSION=checkout-1
tt brief --json
tt task get HBR-1 --json
tt task claim HBR-1 --version 1
tt task checkpoint HBR-1 --version 2 --file docs/example-checkpoint.json
tt task handoff HBR-1 --version 3 --to nate --file docs/example-checkpoint.json
```

Fill in the [checkpoint](docs/example-checkpoint.json) with your actual progress,
next action, workspace, branch and evidence before saving it.

Assignment names the person or agent responsible. A claim records the actor and
session doing the work. Only one session can hold the claim; a competing claim
gets a conflict.

After a lost session, read `tt brief` and the full task. The same actor can use
`tt task resume` with a new session ID and the current version. A handoff saves
the next action, clears the claim and leaves the task ready for its assignee.

Edits check the record's version. Stale edits return **409**. Retry an
unchanged command with the same `--request-id` to avoid duplicate changes.
The CLI also works while the HTTP service is stopped.

## Review the result

Starting work requires a description, acceptance criteria and resolved blockers.
Submitting for review requires a result summary and evidence. Completing it
records the reviewer and an acceptance note.

Comments, attachments and earlier evidence remain in the task's history. Reopen
work when it needs another pass. Archive it when you want it off the active board;
its link and history still work.

## Keep conversations linked

Tasktrack can store references to [Agent Inboxes](https://github.com/natemcguire/agent-inboxes)
threads. Keep the discussion there and save the resulting decision or next action
on the task. Configure a conversation URL to open the thread from task detail.

Tasktrack owns the tasks. Agent Inboxes owns messages and file reservations.
The board works with Agent Inboxes stopped or absent.

## Back up the work

```sh
tt backup /safe/location/tasktrack-backup
```

A backup includes a live database snapshot and every attachment it references.
Restore into a new directory before switching the service over.
See [backup, restore and migrations](docs/persistence.md).

## Run the checks

```sh
python3 -m unittest -v
python3 tests/mutation_checks.py
npm ci
npx playwright install chromium
npm run test:browser
```

Node is used only for browser tests. The checks use disposable databases, real
CLI and HTTP processes, and Chromium. They exercise competing claims, stale edits,
retries, recovery, backup/restore and the desktop and mobile board.

---

[Browser guide](docs/browser.md) · [API and CLI reference](docs/api.md) ·
[Executed API examples](docs/api-examples.md) · [Inbox integration](docs/integration.md) ·
[Verification and screenshots](docs/delivery.md) · [MIT License](LICENSE)
