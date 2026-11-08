---
status: proposed
date: 2026-11-07
implementation: partial
decision-makers: maintainer
---

# 0138: Log structured events with stdlib logging

## Context and Problem Statement

Heinzelmännchen logs with stdlib `logging` and formatted messages (`log.info("automatic update failed: %s", exc)`),
so a log line can be read but not queried, and two lines about the same operation share nothing a tool can join on.
The daemon writes its own JSON lines (`TickLog`), and new work on the desktop app and first-run start has begun to
invent its own event helpers, each with its own field names. Installs, updates, plugin installs and app recovery need
logs that say which step of which operation did what, how long it took and why it failed, without leaking secrets.
What is the one way to log?

## Decision Drivers

- One way to do a thing ([CODING_STANDARDS](../CODING_STANDARDS.md#principles)); no new dependency for it.
- Logs an agent or a support tool can filter by event, operation, plugin and outcome.
- No secret, token or email address in a log, whoever wrote the call.
- Works with the handlers the heads already have (Rich on the terminal, rotating files for the daemon).

## Considered Options

- **Stdlib logging with event names as messages and fields as `extra`, one kernel helper** (chosen)
- **structlog**: a good library, but a new dependency and a second logging pipeline next to stdlib, which every
  head, Uvicorn and third-party library already use; the repository does not depend on it.
- **Keep formatted messages, per-area helpers**: what exists now; the helpers already disagree on field names.

## Decision Outcome

Chosen option: **stdlib logging with event names as messages and fields as `extra`**, because it adds no dependency,
keeps every existing handler and gives one vocabulary that a formatter can write as JSON.

- The message of a log call is the event name, `area.operation.step` in lower-case dotted words
  (`desktop.install.download`, `plugin.install.deps`). Event names are stable: renaming one is a breaking change for
  whoever reads the logs.
- Everything that varies is a field, passed as `extra`: `operation`, `step`, `phase` (`start` | `end`), `outcome`
  (`ok` | `error` | `skipped`), `duration_ms`, `plugin`, `version`, `path`, `error_type`, `error`, `reason` and
  `correlation_id`. New fields use these names where they fit; fields that are `None` are left out.
- `heinzel.kernel._logging` is the helper: `get_logger(area)` gives `heinzel.<area>`; `log_event(logger, name,
  **fields)` logs one event; `step(logger, name, **fields)` logs a start event and one outcome event with
  `duration_ms`, turning an exception into the `error` outcome with `error_type` and the person's message, and
  re-raising it.
- Every event of one operation carries one `correlation_id`: `correlate()` sets it for an operation, and the outermost
  `step` starts one when none is set.
- Fields are redacted by key name: any key containing token, secret, password, api key, authorization, cookie,
  credential, private or session key, or email is written as `[redacted]`, also in nested values and in raw `extra`.
- Levels: `DEBUG` for detail only a developer needs, `INFO` for step starts and ok or skipped outcomes, `WARNING` for
  a degraded result the operation recovered from, `ERROR` for a failed step. The configured `log_level` still filters.
- `JsonFormatter` writes one JSON object per line (`at` in UTC, `level`, `logger`, `event`, fields, `traceback`);
  `TextFormatter` writes `event key=value ...`. A `log_format` setting (`HZ_LOG_FORMAT`, `text` | `json`) picks the
  formatter for the daemon's and the desktop app's log files; the terminal stays text.
- Logger calls under `src/heinzel` never format their message (f-string, `%`, `+`, `.format` or `%`-arguments).

### Consequences

- Good: logs can be filtered and joined by event, operation and correlation id; a failure's log carries the same
  facts as the two-line message the person saw; secrets stay out by construction.
- Good: the existing formatted calls keep working; the JSON formatter also writes records from other libraries.
- Bad: existing formatted calls and the per-area helpers must migrate, area by area; the daemon's `TickLog` and the
  desktop log become `JsonFormatter` handlers and the `log_format` setting is added with them.
- Bad: field names that stdlib reserves on a log record (`module`, `name`, `msg`, `filename`) cannot be used;
  `log_event` refuses them.

### Confirmation

A standards test parses every Python file under `src/heinzel` and fails when a logger call formats its message
outside an allowlist of pre-existing files whose counts may only go down. Unit tests of the kernel helper check event
names, fields, redaction, step outcomes, correlation and both formatters.

## More Information

Built so far: the kernel helper, the formatters and the standards test. Follow-ups: move the desktop app's and the
first-run start's event helpers onto `step`/`log_event`, make `TickLog` a `JsonFormatter` handler, add the
`log_format` setting, export the helper from `heinzel.kernel` for plugins, and shrink the allowlist.
