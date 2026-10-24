---
status: accepted
date: 2026-10-24
decision-makers: maintainer
related: 0018, 0020, 0027
---

# 0031: The CLI checks for a newer release hourly, anonymously and in the background, and says so on stderr

## Context and Problem Statement

People should learn that a newer Heinzelmännchen exists without watching the repository. [ADR 0027](0027-nothing-is-sent-home.md)
forbids update checks with identifying data. The first version of this decision asked only on demand
(`hz system release`) and showed a hint from that cache in `hz --version` and `hz system info`; in practice nobody ran
`hz system release`, so nobody saw a hint. The maintainer asked for the notice to show always, but never in JSON,
cached for an hour, with a short timeout, and without retrying on every command when offline. How does
Heinzelmännchen find out about a newer release, and who tells the user?

## Decision Drivers

- ADR 0027: nothing identifying leaves the machine.
- No command gets noticeably slower, online or offline; an offline machine doesn't ask on every command.
- Machine output (JSON, MCP, REST) stays stable and free of messages meant for people
  ([ADR 0003](0003-heads-are-views-on-operations.md)); pipes stay clean.
- Read-only operations are read-only in every head ([ADR 0019](0019-mcp-and-rest-heads.md)).

## Considered Options

- **The CLI checks at most hourly in the background with a short timeout, caches successes and failures, and prints
  a notice on stderr after human output** (chosen)
- **Ask only on demand (`hz system release`) and show a hint from that cache**: the first version of this decision;
  the cache stays empty unless someone asks, so the hint almost never shows.
- **Check synchronously on every command**: every command waits for the network, and offline for the timeout.
- **`hz system update --check`**: `update` changes the installation, so MCP marks it destructive and REST makes it a
  POST; a flag cannot make one call read-only.
- **Send the user's GitHub token for a higher rate limit**: a token identifies the user to GitHub, which ADR 0027
  rules out for a check nobody asked for; 60 unauthenticated requests an hour are plenty for one check an hour. (The
  user-initiated `hz system changelog --newer` may send one; see below.)

## Decision Outcome

Chosen option: **the CLI checks at most hourly in the background with a short timeout, caches successes and failures,
and prints a notice on stderr after human output**, because people see the notice without asking, while no command
waits and nothing about the user is sent.

- What is sent: one GET to the latest-release endpoint of the package source the installation came from, today the
  GitHub releases API of the repository in the uv tool receipt (the default repository otherwise), with only a
  `heinzel/<version>` User-Agent: no token, no identifier. The system module provides it to the kernel as a
  `ReleaseSource`; heads never import GitHub code.
- When: the `hz` entry point starts the check on a daemon thread when the release cache (`ReleaseCache` in the
  kernel, in the cache directory) was last asked an hour ago or longer. The cache is stamped *before* asking, so a
  check that fails, times out or is cut short when the command ends also waits an hour; a failure is recorded with its
  reason (`hz system doctor` shows it). The timeout is 2 seconds per network operation (urllib's timeout applies to
  connecting and to each read, not in total), and after its own work a command waits at most 2 seconds in total for a
  check still running, then exits and drops it. JSON invocations and the servers start the check too; it writes only
  the cache.
- Who tells: the notice "hz X.Y.Z is available (installed A.B.C): hz system update", with "What's new: hz system
  changelog --newer" on a second line, comes from the cache only. The CLI prints it on stderr after the text output
  of every command, after `hz --version`, and in the footer of `hz` and `hz --help` (the start screen), only when the
  setting is on, the stream is a terminal and no CI variable is set.
  Commands that say it themselves print no notice: `hz system release`, `hz system update`, `hz system doctor` (an
  informational `release` check, which also says why the last check failed) and `hz system changelog` (a header,
  "You're on X; Y is available: hz system update. What's new: hz system changelog --newer", above the installed
  version's notes; the changelog itself asks nothing, and its JSON stays the installed changelog). Never in JSON, MCP
  or REST output; there the version is data: `latest_release` in `hz system info` and the doctor's `release` check.
- `hz system release` always asks, because someone asked, and caches the answer or the failure like the check.
- What a newer release brings: `hz system changelog --newer` shows the release notes of every release newer than the
  installed version, newest first (`--json`: a list of `{version, date, notes, url}`), so people see what they get
  before they update; the notice, the changelog header, `hz system release` and `hz system update` point to it. It
  uses the network only when asked: GETs to the releases API of the same repository through
  `heinzel.modules.system._github`, the `heinzel/<version>` User-Agent, answers of at most 1 MB, at most 4 pages of
  30 releases, 5 seconds per network operation. Because someone asked, it sends their GitHub token if one is set
  (the setting `[system.changelog] github_token`, `HZ_SYSTEM_CHANGELOG__GITHUB_TOKEN`, `GH_TOKEN` or `GITHUB_TOKEN`),
  as `hz system plugin search` does, for GitHub's higher rate limit; only to api.github.com (`_github` refuses any
  other URL and any redirect off api.github.com, so the token never reaches another host or plain http). Without a
  token the requests are unauthenticated and it works the same, at 60 requests an hour. Drafts and pre-releases
  are skipped. Release notes are third-party text: the module removes control characters and invisible format
  characters (bidirectional controls, zero-width and tag characters; the zero-width joiner of emoji stays), so every
  head, MCP and REST included, gets them clean; a release's `url` is kept only if it is a plain release page of that
  repository; each release's notes are cut at 20,000 characters; and in the terminal links show their target.
  Offline or rate-limited, it fails with one error naming the reason and the releases page, exit code 1;
  rate-limited without a token, the error says that `GH_TOKEN` or `GITHUB_TOKEN` raises the limit. Without
  `--newer` the changelog and its JSON are unchanged.
- Opt-out: the setting `update_check` (`[hz] update_check` in the user, site or `HZ_CONFIG` file, `HZ_UPDATE_CHECK`;
  a project's `hz.toml` cannot set it, so a repository can't switch it on or off for the people who clone it),
  default on; `false` turns both the check and the notice off. Any CI variable (`CI`, `GITHUB_ACTIONS`, `GITLAB_CI`,
  ...) turns both off too.
- `hz system release` joins the `hz system` commands of [ADR 0018](0018-one-administrative-namespace.md). A daemon
  job ([ADR 0020](0020-daemon-head.md)) may refresh the same cache later, under the same setting.

This is consistent with ADR 0027: the request carries nothing about the user, and the setting switches it off.

### Consequences

- Good: people learn about a release within an hour of using `hz`, without asking and without waiting.
- Good: offline or rate-limited costs one failed request an hour, never a slower command.
- Good: the kernel only caches a version and knows a `ReleaseSource` by type; GitHub stays in the system module
  (`heinzel.modules.system._github`).
- Bad: GitHub sees one anonymous request an hour from each machine that uses `hz` (its IP address), which the
  setting turns off.
- Bad: a command that ends while the check is still running drops it; the next check is an hour later. The cache is
  replaced atomically, so a dropped check can at worst leave a small `.release.json.<pid>` file behind.
- Bad: installs from other package sources (PyPI) still ask GitHub until the check learns their APIs.

### Confirmation

`tests/unit/kernel/release_test.py` (freshness, stamping, failures), `tests/unit/heads/cli_update_test.py` (one check
an hour, negative cache, the budget with a slow source, CI and the setting), `tests/integration/update_notice_test.py`
(the notice on stderr only, never with `--json`, `--format json` or `output_format = "json"`, the start screen,
`--version`, doctor, the changelog header, and the entry point checking once an hour with a fake source),
`tests/unit/modules/changelog_test.py` (`--newer`: newest first, drafts and pre-releases skipped, bounded pages,
cleaned and cut notes, no token unless one is set, the token from `GH_TOKEN`, `GITHUB_TOKEN` or the setting, never
following a redirect off api.github.com, the rate-limit hint, offline), `tests/integration/update_notice_test.py::test_changelog_newer_*` (text,
JSON and offline through the CLI with a fake GitHub),
`tests/unit/kernel/installation_test.py::test_the_hz_probe_never_checks_for_updates`,
`tests/unit/modules/system_service_test.py::test_release_never_sends_a_token`; the import-linter contract "Heads only
know what the kernel discovered" keeps GitHub out of the heads. The security-reviewer checks changes to the request
against this ADR.

## More Information

- Answers the update-notifier question left open in [ADR 0027](0027-nothing-is-sent-home.md).
- Amended on 2026-10-24: the maintainer decided the check is automatic, cached for an hour (failures included), with a
  short timeout, shown always but never in JSON, and switched off by the `update_check` setting and in CI. The first
  version asked only on demand.
- Amended on 2026-10-24: the maintainer decided "users should see what they get before installing a new release",
  so `hz system changelog --newer` shows the notes of every newer release, and the notice and `hz system update` point
  to it.
- Amended on 2026-10-24: `hz system changelog --newer` sends the user's GitHub token if one is set, to
  api.github.com only, because the shared runners of CI exhausted the unauthenticated rate limit and the README test
  of `--newer` failed; CI passes its read-only `GITHUB_TOKEN`. The hourly check and `hz system release` still never
  send a token.
- `hz system plugin search` is a different, user-initiated search and may send the user's configured GitHub token.
