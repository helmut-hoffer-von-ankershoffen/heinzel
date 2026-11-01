---
status: proposed
date: 2026-10-25
implementation: built
decision-makers: maintainer
amends: 0047
related: 0003, 0039, 0046, 0068, 0074
implemented-by: 188
---

# 0083: Restart and apply verifies the replacement server

## Context and Problem Statement

The web app currently accepts any successful system-info response after requesting a restart. The retiring server
can still answer, and a fresh installation on disk does not prove what code the serving process loaded.
`system.config.show` rereads configuration files, so its answer cannot prove which target settings a replacement
loaded either. A lost POST acknowledgement must not cause an automatic second restart.

## Considered Options

- **An authenticated instance-bound lifecycle check and startup-setting verification** (chosen): prove what serves
  the existing app without adding a process, scheduler or head.
- **Poll system-info or reread installed versions/configuration**: successful responses can come from the retiring
  process or describe files written after startup.
- **Wait a fixed delay and reload**: neither a loaded version nor an applied target is proven, and open work can be lost.

## Decision Outcome

Chosen option: **an authenticated instance-bound lifecycle check and startup-setting verification**, because success
then depends on the replacement's loaded state rather than transport reachability.

- `GET /api/lifecycle` returns an opaque per-server instance, loaded core version and restart capability.
  `POST /api/restart` retains the existing socket handoff and returns the retiring instance. Both inherit the web
  head's bearer, Host, Origin and fetch-site checks, and use `Cache-Control: no-store`.
- `POST /api/lifecycle/verify` compares the requested instance, version and selected safe settings with a startup
  snapshot. The body is limited to 64 KiB before JSON parsing, at most100 settings and bounded field names. Exact JSON
  comparison distinguishes booleans, numbers and strings. Unknown, duplicate and differently loaded values fail.
- Private, password, write-only and secret fields never enter the snapshot, including nested schema references.
  Their guesses receive the same mismatch as unknown keys, even for an authenticated caller. Raw loaded settings
  and validation input values are never returned. Non-JSON or unresolved/recursive schemas cannot be proven here.
- This is transport lifecycle verification, following ADR 0047's head-owned restart. Configuration changes retain
  their existing operations and CLI/MCP/API authorization; the route neither changes settings nor creates another
  semantic path to private settings. Resource measurements remain one shared `system.resource` operation.
- The reusable frontend controller requires explicit confirmation before any restart, preserves work through a
  caller-supplied preparation step where needed, and sends the mutation once. It accepts only a different instance,
  the exact expected loaded version and an instance-bound successful target verification. A timeout, stale target,
  refusal or lost authority cannot become Applied. Polling and requests are bounded and cancellable.
- An uncertain acknowledgement retains the retiring identity. “Check again” only observes; it never replays the
  mutation. The controller keeps the page mounted. Actual draft persistence, reconnect and any deliberate reload
  must be wired and proven by each consuming flow before claiming end-to-end preservation.
- Cache cleanup, when an update flow explicitly requests it, deletes only `hz-shell-` caches. It does not unregister
  workers or touch another application's caches. Core/plugin installation and frontend asset-version proof remain
  separate update-flow work; a successful restart alone is not an update-completion claim.

### Consequences

- Good: an HTTP 200 from the retiring process, stale version or newly edited configuration cannot falsely acknowledge application.
- Good: private settings do not acquire a new equality oracle, and reconnect retries cannot duplicate a restart.
- Bad: settings whose schema hides secrets or cannot be resolved need a separate authorized verification contract.
- Bad: the standalone controller requires integration with settings/plugin/update consumers and their draft owners.

### Confirmation

`tests/integration/web_lifecycle_test.py` covers authentication, origin checks, streamed body limits, startup versus
disk state, strict values and private/nested-secret exclusions. `web/src/lifecycle/controller.test.ts` covers
old-instance responses, dropped acknowledgements, exact version/settings verification, cancellation, bounded hung
requests, observation-only retries and owned-cache cleanup. `RestartAndApply.test.tsx` covers explicit confirmation,
target details, a retained open draft, failure recovery and unmount cleanup. Real rebuilt-server/browser verification,
consumer wiring and independent security review remain release gates.
