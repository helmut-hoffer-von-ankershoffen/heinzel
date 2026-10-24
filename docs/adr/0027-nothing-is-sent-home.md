---
status: accepted
date: 2026-10-23
decision-makers: maintainer
related: 0015, 0020
---

# 0027: Nothing is sent home: no telemetry, crash reports or usage pings

## Context and Problem Statement

Heinzelmännchen runs with a person's rights, reads their team's documents, trackers and meetings, and will run in the
background ([ADR 0020](0020-daemon-head.md)). Usage data would help us prioritise and crash reports would help us fix
bugs, but anything sent to us would leave the user's machine with context from their work. [SECURITY.md](../../SECURITY.md)
already promises "no telemetry". Is that a principle the code is held to, and what does it cover?

## Decision Drivers

- Trust: teams adopt a tool that reads their work only if it does not report on them.
- Tenets 4 and 8 (humans own, open and independent): the user's machine and the services they chose are the only
  places their data goes.
- Debuggability still matters: people need to see what ran, why and at what cost.

## Considered Options

- **Nothing is sent home; observability is local** (chosen)
- **Opt-in telemetry**: still needs an endpoint, a privacy policy and a review of every field, and opt-in data is
  biased towards the users who need it least.
- **Opt-out anonymous usage pings**: breaks the promise in SECURITY.md and the trust the project depends on.
- **Crash reports to a hosted error tracker**: stack traces carry paths, arguments and document content.

## Decision Outcome

Chosen option: **Heinzelmännchen sends nothing home**, because the trust of the teams that run it is worth more than
the data.

- The core sends no telemetry, crash reports, usage pings or update checks with identifying data to the project or
  to any third party. It contacts a network service on the user's behalf: installing and updating through uv, and
  calling the services a workflow or plugin was configured to use. Its one request of its own is the hourly,
  anonymous update check of [ADR 0031](0031-update-check.md), which carries nothing about the user and which the
  `update_check` setting turns off.
- Observability is local: logs, job receipts and `hz system info` / `hz system doctor`. Users who want metrics or
  error tracking export them themselves.
- A plugin that sends data out says so in its README: what it sends and where.

### Consequences

- Good: a simple, checkable promise that matches SECURITY.md.
- Bad: we learn about usage and crashes only through issues and conversations; bug reports must ask for local logs
  and receipts, so those must be easy to collect.
- Bad: first-party plugins and the template carry the same rule, which review must enforce.

### Confirmation

Review only for now: the security-reviewer checks every new network call against this ADR. SECURITY.md states the
promise to users.

## More Information

- [SECURITY.md](../../SECURITY.md) "What Heinzelmännchen does on your machine": "`hz` sends nothing anywhere on its own".
- Receipts and local logs: [ADR 0020](0020-daemon-head.md); health checks: [ADR 0015](0015-health-checks-as-lets.md).
- Decided by the maintainer on 2026-10-23.
- Open question: whether plugins also declare outbound data in `hz system info`. How the update notifier checks
  for new versions within this rule is answered by [ADR 0031](0031-update-check.md) (amended 2026-10-24 by the
  maintainer: automatic, hourly, anonymous, with an opt-out).
