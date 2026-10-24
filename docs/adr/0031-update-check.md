---
status: accepted
date: 2026-10-24
decision-makers: maintainer
related: 0018, 0020, 0027
---

# 0031: The update check is one unauthenticated request on demand; only the CLI shows a cached hint

## Context and Problem Statement

People should learn that a newer Heinzelmännchen exists without watching the repository. [ADR 0027](0027-nothing-is-sent-home.md)
forbids update checks with identifying data and allows network access only on the user's behalf; it left open how an
update notifier fits that rule. `hz --version` and `hz system info` are where people look for versions, and neither
should touch the network. How does Heinzelmännchen find out about a newer release, and who tells the user?

## Decision Drivers

- ADR 0027: nothing identifying leaves the machine; network only on the user's behalf.
- Ordinary commands stay offline and fast.
- Machine output (JSON, MCP, REST) stays stable and free of messages meant for people
  ([ADR 0003](0003-heads-are-views-on-operations.md)).
- Read-only operations are read-only in every head ([ADR 0019](0019-mcp-and-rest-heads.md)).

## Considered Options

- **A read-only `hz system release` asks once, unauthenticated, and caches; the CLI shows a hint from the cache**
  (chosen)
- **`hz system update --check`**: `update` changes the installation, so MCP marks it destructive and REST makes it a
  POST; a flag cannot make one call read-only, and agents would need a confirmation just to look.
- **Check on every command or in the background by default**: network access nobody asked for, and slower commands.
- **Send the user's GitHub token for a higher rate limit**: a token identifies the user to GitHub, which ADR 0027
  rules out; 60 unauthenticated requests an hour are plenty for a check a day.

## Decision Outcome

Chosen option: **a read-only `hz system release` asks once, unauthenticated, and caches; the CLI shows a hint from
the cache**, because it answers the question without sending anything identifying or making ordinary commands
touch the network.

- What is sent: one GET to the latest-release endpoint of the package source the installation came from, today the
  GitHub releases API of the repository in the uv tool receipt (the default repository otherwise), with only a
  `heinzel/<version>` User-Agent: no token, no identifier. Offline or rate-limited is a warning, never a failure.
- The answer is cached in the cache directory (`ReleaseCache` in the kernel), which knows nothing about GitHub.
  `hz system release` always asks fresh, because someone asked; automatic refreshes happen at most every 24 hours.
- A daemon job ([ADR 0020](0020-daemon-head.md)) will refresh the cache, at most every 24 hours and only while the
  setting is on. Until then only `hz system release` asks.
- The setting `update_check` (`[hz] update_check`, `HZ_UPDATE_CHECK`, default on) controls the hint; set it to
  `false` to switch the hint off.
- The hint "vX.Y.Z is available: hz system update" is the CLI head's decision: it appears after `hz --version` and
  the text output of `hz system info`, only when the setting is on, stdout is a terminal and no CI variable is set.
  It is never in JSON, MCP or REST output; `hz system info` carries the cached version as data (`latest_release`).
- `hz system release` joins the `hz system` commands of [ADR 0018](0018-one-administrative-namespace.md).

This is consistent with ADR 0027: the request carries nothing about the user, and it is made only when the user asks
(or, later, by a daemon job the user can switch off with the same setting).

### Consequences

- Good: a checkable promise; ordinary commands never use the network for this.
- Good: the kernel only caches a version; GitHub stays in the system module (`heinzel.modules.system._github`).
- Bad: the hint is only as fresh as the last check, and unauthenticated requests can be rate-limited on shared IPs.
- Bad: installs from other package sources (PyPI) still ask GitHub until the check learns their APIs.

### Confirmation

`tests/unit/modules/system_service_test.py::test_release_never_sends_a_token`, the release tests beside it and
`tests/unit/modules/github_test.py`; the CLI tests in `tests/integration/cli_test.py`
(`test_system_info_json_has_the_latest_release_as_data_but_no_hint`, `test_no_update_hint_when_the_setting_is_off`,
`test_no_update_hint_when_stdout_is_not_a_terminal`, `test_no_update_hint_in_ci`); the import-linter contract
"Modules use only the kernel's public API, like plugins" lists `heinzel.kernel._release`. The security-reviewer
checks changes to the request against this ADR.

## More Information

- Answers the update-notifier question left open in [ADR 0027](0027-nothing-is-sent-home.md).
- `hz system plugin search` is a different, user-initiated search and may send the user's configured GitHub token.
