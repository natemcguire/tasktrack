# Optional conversation links

> **Agent Inbox retired 2026-10-26.** Nate removed the Agent Inbox from every
> project. Tasktrack never depended on it; the `thread_links` metadata below
> remains a generic, optional reference to a conversation thread. Do not build an
> inbox adapter or use inbox reservations.

Tasktrack owns requirements, assignment, board state, dependencies and execution
claims. V1 stores thread references and resolves an **Open conversation** link
when configured. It does not call any messaging service or copy messages.

The task identity used in a link is:

```json
{
  "provider": "tasktrack",
  "instance_id": "the UUID returned by /api/v1/health",
  "task_id": 2,
  "project_id": 1
}
```

Display keys and URLs can change; the instance UUID plus task ID identifies the
record. An `@#2` citation is never an email recipient. Resolve it only with an
unambiguous configured instance/project, or leave it unresolved.

## Link conversations

Task metadata accepts a complete `thread_links` replacement list:

```json
{
  "expected_version": 3,
  "thread_links": [
    {
      "source": "9f3ee2dc-eae5-4ca2-9542-d3c6f137c2d9",
      "project": "harbor",
      "thread_id": "thr_checkout_decision",
      "primary": true
    }
  ]
}
```

There may be at most one primary link. Save the resulting decision or next action
in a task comment/checkpoint. Use separate inbox threads for separate topics.

Add an entry under `inbox_instances` in the Tasktrack data directory’s `config.json`:

```json
{
  "inbox_instances": {
    "9f3ee2dc-eae5-4ca2-9542-d3c6f137c2d9": {
      "thread_url_template": "https://your-inbox.example/its-documented-route/{project}/{thread_id}"
    }
  }
}
```

The URL above is a configuration-shape example, **not a navigation contract**. Set the exact browser URL template documented by your inbox UI. The
available inbox source exposed HTTP thread routes but no documented browser
navigation route, so this build does not guess one. Restart Tasktrack after editing
configuration. `{project}`, `{thread_id}` and `{source}` are encoded when substituted.
Changing the port/base in this template does not rewrite stored identities.

Without configuration, the task still displays the thread identity and explains
that its URL is not configured. No inbox network call happens while loading a board.
