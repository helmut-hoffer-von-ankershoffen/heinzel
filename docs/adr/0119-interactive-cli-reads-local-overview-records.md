---
status: proposed
date: 2026-11-03
implementation: partial
related: 0020, 0031, 0056
---

# 0119: Interactive CLI reads local overview records

## Context and Problem Statement

People running bare `hz` in a terminal need a quick view of scheduled background work, the supervised web server
and sharing. A loaded scheduler is not a continuously running daemon, and a previous successful sharing check does
not establish current reachability. Executing arbitrary plugin observers in a daemon thread cannot enforce a deadline
or prevent writes after the overview returns.

## Considered Options

- **Built-in bounded local observers and declarative historical plugin records** (chosen).
- **Run plugin callbacks with a deadline argument**: arbitrary Python cannot be stopped safely when it ignores it.
- **Call full status or doctor operations**: they can authenticate, use networks or write state.

## Decision Outcome

Chosen option: **built-in bounded local observers and declarative historical plugin records**, because a quick view
must not start plugin work or advertise stale observations as live services.

- Only bare `hz` with terminal input and output selects the overview. Explicit help and redirected use keep their
  previous behavior. This path skips the update check and never invokes operations, setup or presence tracking.
- Local observations share a 250 ms budget after boot. This is not a cold CLI startup guarantee; bounded child
  cleanup can also take time beyond the observation deadline.
- Scheduler state combines installation files, a bounded read-only manager query and a private scheduled tick receipt.
  Manual ticks do not prove the scheduler works. Paused, incomplete, failed and stale evidence stay distinguishable.
- A built-in optional web observer uses current local service identity when available; absence is unknown.
- Plugins add `CachedOverviewLet(name="sharing")` with `module.add`. Core reads at most sixteen private records
  under `state_dir/overview/<qualified-module>.<name>.json`. Files must be owner-only regular files with one link,
  under private physical directories, and no larger than 1024 bytes. No callback or plugin service is resolved.
- Records contain exactly `state` and `observed_at`. State is one of `not installed`, `stopped`, `running`, `unhealthy`
  or `unknown`; no arbitrary detail or URL is displayed. The timestamp must be aware, nonfuture and at most five
  minutes old. Fresh records are still labeled **last observed** with current state **unknown**.
- A plugin writes its sanitized record atomically during an existing explicit status operation or scheduled work;
  the overview never refreshes it. Core has no dependency on a concrete sharing plugin.

### Consequences

- Good: the overview has no plugin observer thread, credential lookup or state mutation.
- Bad: existing plugins need to publish sanitized records before historical sharing evidence appears. The supervised
  web implementation requires a separate adapter; until present the overview states that its observer is unavailable.

### Confirmation

Unit tests cover terminal selection, preserved help, missing observers and terminal control sanitization. Cache tests
reject stale, malformed, oversized, public, linked and nonregular records and prove no plugin service is resolved.
