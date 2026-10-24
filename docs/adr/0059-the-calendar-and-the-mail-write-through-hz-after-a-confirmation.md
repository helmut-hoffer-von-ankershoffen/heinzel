---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0028, 0038, 0042
related: 0024, 0026, 0035, 0045, 0047
---

# 0059: The calendar and the mail write through hz, after a confirmation

## Context and Problem Statement

[ADR 0028](0028-integrations-are-capability-adapters.md) gave `calendar` and `mail` read modes only, and
[ADR 0042](0042-google-workspace-through-the-gws-cli.md) signs `gws` in with read-only scopes. The maintainer asked
(2026-10-24) for a calendar module and a mail module people use every day: create, move, cancel and answer events;
draft, send and file mail; free/busy times and whole conversations. Which methods join the protocols, in which mode,
how do these writes relate to [ADR 0038](0038-drafts-are-approved-in-place.md)'s approval in place and
[ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md)'s confirmation, and which scopes does the
Google adapter ask for?

## Decision Drivers

- Tenet 3 (done, not nagged) and tenet 4 (humans own, with lineage): hz drafts, the person confirms.
- Tenet 9: the protocols stay vendor-neutral; no Gmail or Google Calendar vocabulary in the core.
- ADR 0028's growth rule and [ADR 0026](0026-plugin-api-versioned-with-semver.md): a method added to a mode breaks
  that mode's adapters.
- [ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md): the narrowest scopes, per mode.
- Nothing is sent or changed that the person did not see: no double sends, no silent edits between seeing and sending.

## Considered Options

- **A `write` mode on `calendar` and `mail`, three reads, drafts checked in the core** (chosen)
- **A vendor search method on `calendar`**: one more frozen method every adapter owes, for what `events(window)` and a
  filter answer.
- **Writes as operations of `heinzel-google` only**: ties the calendar and mail modules to one vendor.

## Decision Outcome

Chosen option: **both capabilities gain `write`; the reads the modules need join `read`; writes reach only publish
operations that declared them, and run after the person confirmed what they will do.**

- `calendar`: `free_busy(people, window)` (read, contract-shaped; `people` are comma-separated plain addresses,
  checked in the core by `free_busy_people`, a `Capability.checks` entry like the mail query's);
  `create_event(EventDraft)`, `update_event(event, EventChange)`, `cancel_event(event, notify)`,
  `respond(event, response)` (write). `Event` gains `location`, `response` (the user's own answer, normalized:
  `accepted`, `declined`, `tentative`, or `pending` while unanswered) and `organized_by_user`. No search method.
- `mail`: `thread(thread)` and `draft(draft)` (read, id reads for operations); `create_draft(MailDraft)`,
  `send_draft(draft, seen)` and `relabel(thread, add, remove)` (write). `send_draft` sends a draft that exists only if
  its message is still `seen`, the one the person was shown, so an edit in between sends nothing. `relabel` knows the
  portable names `inbox` (removing it archives) and `unread` in every adapter; other names are the vendor's labels or
  folders. Nothing deletes mail or files it into spam or trash.
- The drafts are checked in the core (`EventDraft`, `EventChange`, `MailDraft`, `plain_addresses`): plain addresses
  only, at most 100, one-line titles and subjects without control characters, bounded texts. Adapters pass addresses
  they derive (a reply's recipients) through `plain_addresses` too.
- Approval: for mail, the draft in the mailbox is the draft in place (ADR 0038): `draft` reads its state and
  `send_draft` is the approval. The calendar has no place to draft in: the confirmation is the approval, `--confirm`
  on an operation that otherwise only shows what it would do (as [ADR 0047](0047-plugins-are-managed-from-the-web-app-after-a-confirmation.md)'s
  `add`), and in a chat the `ActionGate`, which holds every write. This amends ADR 0038 §2: `place_draft` and
  `draft_state` belong to capabilities that place drafts in documents, trackers and sheets, not to `calendar` and
  `mail`.
- Growth: the new reads break `calendar:read` and `mail:read` adapters written before them. Before 0.1.0 none is
  released, so they are added at once (ADR 0026's deprecation cycle applies from the first release); the repository's
  test adapters gained them.
- Google (amending ADR 0042): writes through `calendar events insert/get/patch/delete` on `primary` and
  `gmail users drafts create/send` and `users threads modify`; reads through `calendar freebusy query`,
  `gmail users threads get` and `users drafts get`. A request body is `--json <JSON>`. A write is retried only after a
  rate limit (Google refused it); one that timed out or failed without a refusal (5xx, no answer) says it may have
  happened and to look first. A new event carries an id of hz's choosing, so a repeated insert is refused, never
  doubled. A recurring series' own id is refused for moves and cancellations: only one occurrence changes. Ids are
  checked against Google's alphabets before they reach `gws`. The scopes `calendar.events` and `gmail.modify` (which
  reads too and replaces `gmail.readonly`, and can't delete mail for good) are asked for only when the person lets hz
  write (`hz setup google`, "no" by default); a missing one names the `gws auth login` that adds it and how to keep
  the other scopes.

### Consequences

- Good: the first everyday modules without a vendor in core code; another provider binds in.
- Bad: every future `calendar` or `mail` adapter owes more methods, and maps the portable label names.
- Bad: `gmail.modify` covers all of a person's mail; it is opt-in, and the setup question says what it allows.

### Confirmation

`tests/unit/kernel/capabilities_test.py` (modes, contract shape, no delete, the `free_busy` check),
`tests/unit/kernel/calendar_test.py` and `tests/unit/kernel/mail_test.py` (draft checks),
`plugins/heinzel-google/tests/unit/{calendar,mail,gws,setup,contract}_test.py` (requests, refusals, retries, scopes)
and `plugins/heinzel-google/tests/integration/capability_test.py` (only a publish operation that declared
`calendar:write` creates an event).

## More Information

- **The calendar module** (`hz calendar`, built in): today, `week`, `show`, `search`, `free`, `now` (the countdown the
  web app's footer shows) and the writes `new`, `move`, `cancel` and `respond`, each answering what it would do
  until it runs again with `--confirm`. Reads go to the CLI, MCP and the web head, never the unauthenticated REST
  head; writes to the CLI, MCP over stdio and the web head. Reads over MCP's streamable HTTP, which has no
  authentication, are an accepted risk, as for `hz org agenda`: that transport is started on purpose, on localhost. `hz calendar remind` records a `meeting` notice
  (`[system.notify] meeting`), urgent so working hours don't hold it, `[calendar] remind_minutes` (0, or at least 5)
  before a meeting, with its join link. The daemon's notify step runs it at every tick, as it runs doctor hourly: a
  job would write a receipt every five minutes for everyone. In a chat, a proposed call of an operation with a
  `confirm` parameter is proposed with `confirm` set: the person's confirmation is the confirmation.
- **Optional publish operations** (amending ADR 0028's "only read-only operations can be optional"): a built-in
  module that writes a capability nobody connected must not make doctor degraded for everyone. A publish operation
  may be `optional=True`: while nothing is bound, its unmet requirement is no doctor finding, and an unserved handle
  refuses on every method, so nothing is written; once served, its adapter is built when it is injected, as before.
  Doctor shows a module's unconnected optional uses of one capability as one check.
