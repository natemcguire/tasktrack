# F08 — Notifications and reliable email

**Wave:** 2 · **Dependencies:** F02, F06 · **Review:** product owner + operations

## Outcome

Important changes reach the right person once, with a useful link and controllable
noise. Support in-app and email notifications for invitations, invoice issue/payment
status, Comms/questions/replies, mentions, assigned work, requested approval,
blocked work and runner failures. Email delivery is not equivalent to reading.

## UX and preferences

[Notification settings](../wireframes.html#notifications). Preferences per tenant:
mentions/direct replies immediate by default, followed comments immediate or daily
digest, assigned-task status daily digest, finance events immediate for billing
roles, runner failures in-app immediate. A user can follow/unfollow an item and
mute ordinary comments. Transactional sign-in/invitation/security and issued
invoice delivery remain distinct from optional activity subscriptions.

In-app inbox shows unread, category, time and Open. Email has agency identity,
short safe summary and one deep link. Invoice email names invoice number, amount,
due date and payment link; it does not attach internal cost reports. Comment email
includes a bounded shared snippet only if the recipient still has access. Include
preference/unsubscribe links for optional activity; no tracking pixel by default.

## Event-to-recipient rules

| Event | Eligible recipients | Deduplication |
| --- | --- | --- |
| Invitation | Exact invited email | invitation+send generation |
| Comms received/reply | Submitter, assigned staff, followers | event+recipient |
| Mention | Explicit visible mentioned person | comment+mention generation+recipient |
| Task assignment/approval | Named assignee/approver | event+recipient |
| Invoice issued/reminder | Designated invoice recipients | invoice+issue/reminder operation |
| Payment received/failed/refund | Invoice billing contacts and internal billing | authoritative payment transition |
| Runner blocked/auth failed | Owner and authorized maintainers | run+failure generation |

Exclude the actor from ordinary self-notifications. Mention wins over generic
comment notification for the same person/event. No `@all` in v1. Customer recipients
must have current project and relevant billing grants at send time. An invoice's
explicit external billing email must be validated as an authorized billing contact,
not copied from arbitrary comment text.

## Storage and processing

`notification(id,event_id,recipient_membership_id,category,resource_ref,read_at)`;
`delivery(id,notification_id,channel,template_version,status,attempts,next_attempt_at,
provider_message_id,last_error_code)`; unique(event,recipient,channel).
Preferences are versioned. Tenant transactional outbox commits with the originating
mutation; dispatch and send are outside the transaction. Delivery states queued,
sending, sent, delivered-if-confirmed, bounced, failed, suppressed. Never invent
provider delivery callbacks if the configured email provider does not expose them.

Use Cloudflare email binding through a deployment-configured verified sender.
Tenant display/reply-to names require verification; no arbitrary spoofable From.
Digest groups only still-visible events and skips already-read optional items.
Retry transient errors with jitter at1m,5m,30m,2h,12h then dead-letter. For an
ambiguous send timeout without provider idempotency, reconcile provider evidence
where available; otherwise mark uncertain and avoid blindly duplicating invoices.

## API and failure handling

`GET /notifications`, `POST /notifications/{id}/read`,
`GET/PATCH /notification-preferences`, `POST /resources/{type}/{id}/follow`,
`GET /deliveries?resource_id=` (sender/finance/admin only),
`POST /deliveries/{id}/retry` (authorized operator, reason and idempotency).
Inbound email replies are deferred: email links lead to the authenticated composer.
Do not advertise a reply-by-email address that is not implemented.

## Acceptance and rollout

1. Every event in the table has a tested recipient rule, template and deep link.
2. Duplicate/out-of-order queue events produce one intended notification; actor
   exclusion and mention/comment coalescing work.
3. Revoke access after enqueue: no snippet or attachment leaves the service.
4. Provider outage/bounce/unknown timeout is visible and retryable without falsely
   claiming delivery or a customer read.
5. Preferences/digest boundaries honor tenant timezone and daylight-saving changes.
6. Render emails at phone widths, dark mode and plain text; verify actual magic
   link, invite, comment and invoice delivery in designated test inboxes.

Migrate existing sign-in email behind the shared sender adapter without changing
scanner-safe token redemption. New notification delivery is feature-flagged per
tenant until test-inbox evidence is recorded. Track queue age, bounce and duplicate
rates with no message-body logging.
