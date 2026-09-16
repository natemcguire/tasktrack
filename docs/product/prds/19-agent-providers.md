# F19 — Claude, Codex, Grok and Antigravity adapters

**Wave:** 5 · **Dependencies:** F18 · **Review:** provider qualification

## Outcome

Run approved installed agent CLIs through a common adapter while respecting their
actual authentication, usage limits, output formats and supported automation modes.
Use subscription access when the provider supports the intended account/workload;
do not promise unlimited or free inference, share logins across tenants, or silently
fall back to paid API requests. All four providers are optional capabilities.

## Adapter interface

An adapter implements `probe()`, `start(spec)`, `resume(session_id,spec)`,
`cancel(attempt_id)`, `parse_event(bytes)`, `usage_delta()` and `checkpoint()`.
Probe returns executable/version, platform support, configured auth mode (no
secret), model IDs, resume/structured-output capability and qualified cost source.
Profiles pin tested CLI/model/config versions; upgrades run contract fixtures before
activation. User input is passed as argv/stdin, never interpolated into a shell.

Normalized events: started, text_delta, tool_request, tool_result, usage,
checkpoint, approval_required, blocked, completed, failed. Buffer malformed lines
with bounded size and redact tokens; unknown event types are retained by digest
for diagnosis and cannot turn into arbitrary actions. Terminal success requires a
valid final result and exit state, not a matching phrase in output.

## Initial command contracts to qualify

| Provider | Start / resume shape | Important boundary |
| --- | --- | --- |
| Claude Code | `claude -p … --output-format stream-json`; explicit `--resume <id>` | Subscription and API/bare modes differ; verify active auth and current allowance |
| Codex | `codex exec --json …`; `codex exec resume <id> …` | `-p` means profile, not print; observed usage in events; saved-account automation has deployment restrictions |
| Grok Build | `grok -p … --output-format streaming-json --no-subagents`; explicit `--resume <id>` | Qualify installed CLI/platform and OAuth entitlement; do not assume consumer plan equals unrestricted API credit |
| Antigravity | `agy --print … --output-format stream-json --sandbox`; `--conversation <id>` | Qualify Linux distribution, model IDs and headless permission behavior |

These are reviewed interface shapes, not a promise that every flag combination
works on every installed version. Build per-version argv arrays from `--help` and
official docs, then smoke-test a harmless repository before enabling the profile.
Never use a global “latest conversation” to resume project work. Disable subagent
spawning by default; a future tenant policy can explicitly permit a bounded fan-out.

## Authentication and subscription accounting

Admin connects their approved account through the provider's supported interactive
or device flow on a trusted operator surface. The runner stores provider auth in
a protected per-tenant profile, outside repository/checkpoint bundles. Do not paste
auth files into tasks, ship cookies in public CI or distribute one person's login
to customer tenants. API-key mode is a separate explicit profile with a separate
budget; missing/expired subscription auth blocks the run.

Official Codex documentation supports noninteractive execution and explicit resume,
but warns against its advanced saved-account CI workflow for public/open-source
repositories. Qualify a supported private operator setup; use an explicitly approved
alternative where restrictions apply. [Codex noninteractive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).
Claude documents `-p` and distinct bare-mode auth. [Claude headless documentation](https://code.claude.com/docs/en/headless).

F10 records observed tokens when available, estimated coverage otherwise. Subscription
allowance percentages alone cannot yield an accurate per-task token count. Preserve
API-equivalent cost separately from actual subscription allocation. An unknown price
or model ID is unavailable, not zero. Auth/quota diagnostics must not expose secrets.

## UI and API

[Runner/provider screen](../wireframes.html#runners) shows connected, expired,
unsupported, quota paused and needs qualification. Admin can choose a model from
probe results, default sequential limit and timeout. `GET /provider-profiles`,
`POST /provider-profiles/{id}/probe`, admin configure/disable, and
`GET /provider-profiles/{id}/qualification` expose the same states to agents.
No model output can edit these profiles or approve new access.

## Acceptance matrix

For each provider independently: supported Linux install; intended subscription
entitlement documented; harmless headless run; exact session resume; structured
event parsing; usage/error fixtures; permission-denied behavior; auth expiry;
rate limit; cancellation; secret redaction; no automatic API fallback. Record
version/date/source and mark failures unsupported/blocked. A provider is not
enabled just because its binary exists locally. End-to-end browser/API tests show
the blocked reason and recovery action while other providers remain available.
