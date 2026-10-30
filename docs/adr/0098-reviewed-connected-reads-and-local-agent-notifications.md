---
status: proposed
date: 2026-10-30
related: 0028, 0043, 0045, 0049, 0067, 0068, 0094
---

# 0098: Review connected reads and local agent notifications separately

## Context and Problem Statement

A durable agent can receive a user message and, after a separate decision, run recurring turns in its own session
([ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md),
[ADR 0067](0067-hz-owns-sessions-across-pydantic-and-native-cli-turns.md)). The person may want it to inspect a
connected source and queue a local notice, but accepting contract text alone does not identify an account, grant a
read, activate a schedule or approve notifications. The kernel needs a generic provider contract that can express
and recheck those separate decisions without trusting model-supplied authority.

## Decision Drivers

- Keep source, account, operation, arguments, cadence and local notification bounds visible before the relevant decision.
- Preserve existing local notification policy and older schedules without silently widening either.
- Record uncertain effects and preserve private session ownership across interruptions.

## Considered Options

- **Exact connected-read and schedule decisions with bounded local notification consent** (chosen).
- **Treat an accepted contract as permission for connected reads and notifications**: text alone does not establish the
  selected source, account, execution limits or the person's intent to allow those effects.
- **Require a separate human confirmation for every recurring local notice**: prevents a separately reviewed bounded
  recurring notification policy from carrying out its intended task.

## Decision Outcome

Chosen option: **exact connected-read and schedule decisions with bounded local notification consent**, because
these decisions bind the authority a recurring turn needs without giving its model an approval issuer.

- The kernel's public connected-read contract lets each owning provider declare supported public read methods. The
  host checks the actual selected module, method and schema; a declaration is support metadata, not authority or
  publisher authentication. An owner-bound review records the exact source, observed account and provider scopes,
  plus host-controlled arguments. A rolling window is a distinct reviewed variant with one direct `Window` parameter,
  a positive span of at most 31 days, and bounds derived from a host UTC instant. Changes to the approved source,
  account, argument policy or grant stop later reads. A provider-specific credential and installation boundary must
  be reviewed separately before enabling a real integration.
- A person separately confirms the exact contract and recurring schedule. A schedule may include an already approved
  connected read and an optional local-notification policy; omission grants neither. Existing fixed-notice schedules
  and older recurring schedule approvals do not acquire new capabilities. The daemon rechecks the current binding
  and owner while holding the session's main writer before each turn and effect.
- A conversational notice is an owner-private draft until a person confirms that exact title and body. Recurring turns
  may queue local notices only under a newly reviewed policy with at most one attempt per turn, a rolling 24-hour
  attempt limit and a same-topic cooldown. The model chooses the bounded topic; it is not a verified source identity
  and cannot guarantee semantic deduplication. The host fixes the local route and notification kind; no external
  message destination or urgent override is granted.
- Before queueing, record the attempt and its exact private body. A failed or interrupted acknowledgment remains
  uncertain and is not automatically resent. The owner can inspect a durable receipt or explicitly set aside an
  uncertain attempt without undoing a possible effect. A queued notice remains subject to the existing local policy;
  it does not prove that an in-app or native display appeared. Unresolved same-topic attempts block later attempts
  until the owner resolves them.

### Consequences

- Good: people can review and revoke connected access separately from scheduling and local notification behavior.
- Bad: an unavailable or changed provider, unresolved attempt or exceeded limit can stop work until inspected. The
  current history projection reads the session journal; it is not an indexed, lifetime-scale search interface.

### Confirmation

`tests/unit/modules/connected_grants_test.py` checks exact grants, selected provider declarations, rolling windows and
revocation. `tests/unit/modules/autonomous_schedule_test.py` checks separate schedule consent and occurrence fences.
`tests/unit/modules/notification_proposals_test.py` and `tests/unit/modules/scheduled_notifications_test.py` check
owner-bound notice decisions, attempt limits and uncertain recovery. The fake-provider compiled-daemon integration
in `tests/integration/scheduled_connected_read_test.py` checks combined consent, restart and revocation. Mounted
phone journeys in `tests/e2e/scheduled_notification_review_test.py` exercise the review and recovery UI with fake
providers; real provider installation and native display remain separately reviewed.
