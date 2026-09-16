# Provider research and qualification

Checked16 September2026. Product and provider behavior changes; recheck the cited
primary documentation before implementing or activating an adapter. Installed CLI
help is evidence of local flags, not proof of Linux support or account entitlement.

## Payments

- Stripe's [direct-charge model](https://docs.stripe.com/connect/direct-charges)
  is the proposed tenant-owned account boundary. [Webhook processing](https://docs.stripe.com/webhooks)
  needs signature verification, durable receipt and duplicate/out-of-order handling.
- A [two-step confirmation flow](https://docs.stripe.com/payments/build-a-two-step-confirmation)
  provides a starting point for method collection followed by explicit confirmation.
  F13 must qualify funding metadata and the exact quote/Intent sequence in sandbox;
  basic hosted Checkout alone is not assumed to enforce a conditional surcharge.
- [Visa's published U.S. guidance](https://usa.visa.com/dam/VCOM/global/support-legal/documents/merchant-surcharging-considerations-and-requirements.pdf)
  excludes debit/prepaid and caps credit surcharges at the lower of acceptance cost
  or3%. This is not a complete jurisdiction rulebook. Invoice-level configuration
  must reject unsupported rates and retain the rule/evidence version.

Specific banking provider research, account eligibility and source documents stay
in private tenant notes. The public contract distinguishes instructions, read-only
feeds, hosted receivables, inbound debit and outbound transfer capabilities. A
bank's outbound ACH endpoint does not prove customer collection is supported.

## Accounting

[QuickBooks Online developer documentation](https://developer.intuit.com/app/developer/qbo/docs/get-started)
is the initial adapter reference. Start with a versioned interchange export; add
OAuth realm-scoped mappings and sandbox sync only after an explicit connection
decision. No accounting account is connected by this design package.

## Agent execution

| Provider | Confirmed evidence | Still needs qualification |
| --- | --- | --- |
| Codex | Installed `exec`, JSON events, explicit session resume; [official noninteractive docs](https://learn.chatgpt.com/docs/non-interactive-mode), [authentication](https://learn.chatgpt.com/docs/auth) | Approved account automation setup, Linux image, credential isolation and usage reconciliation |
| Claude Code | [Headless `-p`](https://code.claude.com/docs/en/headless), [authentication modes](https://code.claude.com/docs/en/authentication) | Current account allowance, per-version output, supported unattended setup and failure recovery |
| Grok Build | Installed help exposes `-p`, structured output, `--resume`, `--no-subagents`, OAuth | Official distribution on runner, account eligibility, exact usage events and price mapping |
| Antigravity | Installed help exposes `--print`, structured output, `--conversation`, `--project`, sandbox; local model list includes Flash | Linux availability, permitted subscription automation and precise usage/recovery behavior |

Codex's advanced saved-account CI guidance warns against using that workflow for
public/open-source repositories. The design does not copy account auth into public
CI. A private operator setup still needs qualification. Subscription access has
limits and actual cost; no adapter may silently fall back to API billing. The
normal app remains useful if a provider is unsupported.

## Runner hosting

[Cloudflare Sandbox](https://developers.cloudflare.com/sandbox/) supplies isolated
Linux execution, background processes, Git and persistence options. It is a viable
adapter candidate, not the same runtime as a Python Worker. Qualify persistent
provider login, idle/restart behavior, storage and observed cost before choosing it.

A small private VPS can be simpler for persistent CLI profiles and sequential jobs.
Compare x86 compatibility, memory during builds/browser tests, backups, bandwidth,
region and total recurring price. Start with one job at a time; choose8GB if4GB
cannot run the representative workload. A single agency's trusted runner is not
a multi-tenant sandbox service. Record actual quotes and operational host details
privately, not in source. No VPS purchase or provisioning is part of design review.
