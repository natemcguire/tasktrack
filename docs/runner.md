# Private development runner

A small optional Linux host for one manually started agent or build at a time.
The application stays on Cloudflare. This setup does not implement the job API,
customer isolation, project authorization or unattended execution from F18–F20.

## Base host

Use an Ubuntu 24.04 x86_64 VM with 2 vCPU, 4 GB RAM and 40 GB disk. Render
[cloud-init](../infra/runner/cloud-init.yaml) with a dedicated SSH **public** key.
Keep the rendered file, private key, host address and provider IDs out of Git.

The template creates:

- `operator`: key-only administrative access; no root or password SSH.
- `runner`: non-admin account with private project/checkpoint directories.
- A firewall accepting SSH only, automatic security updates without surprise reboots,
  4 GB swap and bounded system logs.
- An `agent-jobs.slice` budget: 3 GB memory, 2 GB swap, 150% CPU and 512 tasks.

Swap is a crash buffer, not extra performance. Start with lightweight jobs. Large
builds and browser workloads require measured memory checks before increasing size.
No paid backup/volume/load-balancer add-ons are required for this initial setup.
Until an off-host checkpoint backup is configured and restored successfully, keep
this host disposable: push reviewed code and never store the only copy of work here.

## Manual jobs

Install Node 24 LTS plus the official Codex and Claude CLIs with
[install-tools.sh](../infra/runner/install-tools.sh). The installers run as runner;
no subscription login or API request is performed. Record installed versions.

Install [runner-job](../infra/runner/runner-job) as root-owned mode0755 at
`/usr/local/bin/runner-job`. Create a runner-owned project checkout, then:

```sh
sudo runner-job /srv/tasktrack/projects/example git status --short
sudo runner-job /srv/tasktrack/projects/example codex --version
```

A host-wide file lock rejects overlapping jobs with exit75. Commands run as the
unprivileged runner in the resource-limited slice. They can access other projects
owned by that same runner, so this is **one trusted agency's development host**,
not a sandbox for unrelated tenants or hostile customer code. No web shell or
public agent endpoint is installed.

Use `sudo -iu runner` for provider sign-in. Install supported official CLIs; keep
credentials in that account's private home. Provider authentication is separate
from installation. Do not copy local subscription credentials to the host or add
API keys as a fallback. No prompts are submitted as part of infrastructure setup.

## Verification and upkeep

Check `cloud-init status --long`, `sshd -t`, effective SSH authentication settings,
`ufw status`, swap, free disk and the absence of unexpected public listeners.
Smoke-test one job as runner, a concurrent rejection and the resource slice.
Reboot once after provisioning and repeat the connectivity/firewall check.

Keep operator keys on the admin machine, rotate them deliberately, and treat
provider rescue/console access as the recovery path if a key is lost. Record
actual host identity, pinned SSH fingerprint, installed versions, cost, outstanding
logins and backup status in a private handoff. Recheck price/capacity before resizing.

The cloud-init template explicitly reuses the existing `operator` group. On Ubuntu,
that group may exist before the user; omitting `primary_group` makes user creation
fail and can lock out SSH after the allowlist takes effect. Validate on the actual
image and retain provider recovery access.

## Provisioning evidence

The first Ubuntu 24.04 host passed key-only operator access, root/password SSH
rejection by effective policy, active host firewall and security updates, 4 GB
swap, and the configured CPU/memory/task limits. Manual jobs ran as `runner`;
a simultaneous second job returned75, and a project outside the permitted root
returned64. A clean public Tasktrack checkout was prepared without Git write
credentials. Installed tools: Node24.21.0, Codex0.154.0, Claude Code2.1.267.

These checks do not qualify authenticated provider execution, browser workloads,
backup restore, customer isolation or automatic job dispatch. Those remain review
items. Provider installs follow the [official Codex instructions](https://learn.chatgpt.com/docs/codex/cli)
and [Claude setup guide](https://code.claude.com/docs/en/setup); Node archives are
verified against their published SHA256 file before extraction.

Codex sandbox smoke testing also passed as runner after installing distribution
Bubblewrap and its Ubuntu24.04 AppArmor profile, following the [official sandbox
prerequisites](https://learn.chatgpt.com/docs/sandboxing). The host-wide unprivileged
user namespace restriction remains enabled. This verifies local sandbox startup,
not an authenticated model session.
