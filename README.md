# Tasktrack

A kanban board for people and coding agents. Keep the requirements, owner
and next action with the task.

<a href="docs/screenshots/board.png"><img src="docs/screenshots/board.png" alt="Tasktrack board" width="560"></a>

## What it does

- Organize projects, epics and PRDs. Add tasks, comments and files.
- Move cards from backlog to done. Assign owners and claim work atomically.
- Save progress and hand off to another session without losing the thread.
- Share a customer update with a picture and a link to the task.

Browser, CLI and HTTP API. Python and SQLite. Hosted on Cloudflare or run locally.
[MIT licensed](LICENSE).

## Get started

[Open Tasktrack](https://tasks.eastbayprojects.com). Enter your email, follow the
sign-in link, and start a workspace.

Or run it locally with Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv tool install git+https://github.com/natemcguire/tasktrack
tt serve
```

Open [localhost:7777](http://127.0.0.1:7777/). Data lives in `~/.local/share/tasktrack`.

## Use the CLI

Create a project, add a task and claim it:

```sh
export TT_ACTOR=codex TT_SESSION=homepage-1

tt project create DEMO "Demo"
cat > task.json <<'JSON'
{
  "title": "Build the homepage",
  "description_markdown": "Add a homepage with a signup link.",
  "acceptance_criteria": ["The signup link opens the signup form."]
}
JSON
tt task create DEMO --file task.json
tt task claim DEMO-1 --version 1
tt brief --json
```

Use the task reference and current version returned by your board. A claim belongs
to one session; `tt brief` shows your work when you come back.

For your hosted workspace, create a token under **Account → Agent access**:

```sh
export TT_URL=https://tasks.eastbayprojects.com
export TT_TOKEN=your-token
tt brief --json
```

[Browser guide](docs/browser.md) · [API & CLI](docs/api.md) ·
[Backups](docs/persistence.md) · [Inbox integration](docs/integration.md) ·
[Development & tests](docs/delivery.md) · [Cloudflare](docs/cloudflare.md)
