---
status: proposed
date: 2026-10-25
decision-makers: maintainer (the decisions), architect (shape)
amends: 0054
related: 0020, 0024, 0026, 0027, 0049
---

# 0062: State repositories: one writer per branch, append-only segments, the daemon pushes

## Context and Problem Statement

A team wants to know who actually uses hz: who onboarded and who is active day by day. hz has no server and sends
nothing home ([ADR 0027](0027-nothing-is-sent-home.md)); git hosts the team already trusts are the only
place to share state. [ADR 0054](0054-observability-is-whether-each-team-promise-is-kept.md) §4 copies telemetry
logs into the team checkout by hand (`hz report sync`) and names automatic sync as the next step;
[ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md) §10 puts machine state on an
orphan branch of the team repository. Pushing to a repository's `main` from a daemon fights its branch protection
and races every other writer. How does hz share machine state through git without conflicts, without a server, and
without turning usage into a measure of people?

## Decision Drivers

- Conflict-free by construction: no merge, rebase or force-push on any write.
- Offline first: the local files are the queue; nothing is lost while the machine sleeps or is offline.
- Works with any git remote (a bare repository on disk too); credentials stay git's.
- Flow, never performance ([ADR 0024](0024-people-and-org.md) §7): nothing finer than a
  day, aggregate by default, a person named only with their consent.
- Small: no new runtime, dependency or hosted service.

## Considered Options

- **One orphan writer branch per user and machine, append-only segments, pushed fast-forward by the daemon** (chosen)
- **Per-writer files on `main`**, ADR 0054 §4 automated: every push races the others' commits and needs a fetch and
  rebase, and a daemon would need push rights past `main`'s protection, or a pull request every 15 minutes.
- **GitHub's REST API as an append store** (Contents API with the blob SHA as compare-and-swap): no offline mode, a
  token in the process, and a team of daemons trips the secondary rate limit on content creation.
- **A versioned database** (Dolt, Irmin, CRDT documents in git): a new runtime and hosting outside the git host, and
  opaque history that can't be deleted branch by branch.

## Decision Outcome

Chosen option: **one orphan writer branch per user and machine, append-only segments, pushed fast-forward by the
daemon**, because one writer per ref can't conflict, the local streams already are the queue, and a person's data is
one branch they can delete. This slice builds the personal sink; the team sink follows behind its own consent.

1. **Writer branches.** A state repository holds a reviewed `main` that hz never pushes to, and branches
   `hz/<user>/<machine>`, each an orphan with exactly one writer: hz on that machine for that user. `<user>` is the
   roster handle in `[report] user`; `<machine>` is eight random characters made once in the state directory, never
   the host name. The branch holds `hz-state.json` (`v`, `user`, `machine`, `hz`, `streams`) and one segment per
   stream and month, `<stream>/<YYYY-MM>.jsonl`, laid out exactly as `<state dir>/state/` is locally.
2. **Streams** are declared by modules, never discovered, and each line is strict metadata validated by its model on
   write and again before every push. `system.state` declares two: **presence**, one line per local day on which hz
   was used on this machine (`{"v":1,"date":"2026-10-25","hz":"0.4.0","kind":"active"}`), and **adoption**, a line
   when hz is first seen (`installed`) and one per new version (`upgraded`), with the OS family. Lines carry no user or
   machine (the branch names both), no time of day, count, command, path, host, address or cost. "Used" is a person:
   every head notes it through the kernel's `Presence` before it runs an operation (CLI, MCP, the API and web heads),
   except for the daemon's own module, whose ticks the OS starts. Presence never fails or slows an operation.
   **Telemetry** (ADR 0054's entries) becomes a third stream when the wake loop writes it, in a later slice.
3. **The push.** The daemon job `system.state:push` runs every 15 minutes and waits up to 90 seconds at random first,
   so a team's machines don't push together; `hz system state push` pushes now. It fetches only its own branch into a
   clone below the data directory, checks that every segment there is a prefix of the local segment, copies the local
   bytes, commits once (`state: <n> new line(s) in <k> segment(s)`, author `<handle> <<handle>@hz.invalid>`, hooks
   and signing off) and pushes `refs/heads/hz/<user>/<machine>` without force. Nothing changed, nothing is pushed.
   Every git command goes through the kernel's hardened runner (`GitRunner`, moved from the team module: no prompts,
   no submodules, no `ext::`, allowlisted environment, 300 s timeout); credentials are git's own and hz never reads,
   stores or prints one; errors are kept with any `user:password@` removed.
4. **Failures and refusals.** A failed push is recorded and backs off, 15 minutes doubling to a day; doctor reports a
   repository that took no push for two days. A branch with commits this machine didn't make, or a segment with
   lines the local one lacks, **stops** the sink (HZS001): hz never merges, rebases or overwrites it. `hz system state
   reset` moves the clone aside (kept as evidence) and starts again from the local streams, which stay the truth.
5. **The personal sink** is a private repository the user names, `[system.state] personal` (no default; nothing
   leaves the machine until it is set). hz holds no repository name of anyone. A project `hz.toml` can't set it.
6. **The team sink** (next slices) exists only behind explicit consent: off until the person says yes, then aggregate,
   and their handle may appear in the team's views only after a second, separate yes. Team state goes to writer
   branches of the team repository by default, beside its `main`, and a team can name another repository instead.
   A team copy is the stream filtered by the stream's own pure rule, and readers check the prefix rule on that
   projection. `hz team adoption` reads the team's branches into aggregates: weekly active people of the roster,
   onboarding, versions; no view only a manager has.
7. **This amends [ADR 0054](0054-observability-is-whether-each-team-promise-is-kept.md) §7**: its automatic sync is
   this push; `hz report sync` stays as it is until the telemetry stream and the team sink replace it.

### Consequences

- Good: no write can conflict; offline machines push when they wake; deleting a person's data is deleting their
  branches, and `main` holds only what people reviewed.
- Good: any git remote works, a bare repository on disk too, so the acceptance tests run real git without a network.
- Bad: GitHub removes a deleted branch's objects only eventually, and a fork or clone keeps them; revocation must say
  so.
- Bad: a reader fetches one branch per machine; for large teams that is many refs, and aggregates on `main` (a later
  slice) become the read path for old ranges.
- Bad: the kernel's public API grows by `Presence` and the git runner (`GitRunner`, `CommandFailed`, `RunCommand`,
  `check_repository_url`, `git_command`, `GIT_HARDENING`) (ADR 0026).

### Confirmation

`tests/unit/modules/state_streams_test.py` (one presence line a day, installed then upgraded once per version, links
refused, line validation); `tests/unit/modules/state_service_test.py` (no repository or handle is a skipped run,
backoff, jitter, the daemon module never counts, scrubbed errors, status and doctor); `tests/integration/state_test.py`
(real git against a local bare repository: the first push creates the orphan branch, the second is a fast-forward or
nothing, a foreign commit stops the sink, `reset` recovers after the branch is deleted, the daemon tick runs the job,
a CLI command notes presence and the daemon's tick doesn't); `tests/unit/kernel/presence_test.py`; `tests/unit/public_api_test.py`; the import-linter
contracts list `system.state` and the kernel's `_git` and `_presence`.

## More Information

- Research and the full design, including the read path (shallow fetch of `hz/*`, a derived SQLite index) and the
  slices after this one: telemetry as a stream written by the wake loop; `hz team adoption` and its views; the team
  sink by consent with revocation that deletes the person's branches; integrity (a hash chain per segment, signed
  commits); aggregates on `main`.
- Prior art: git-bug (operations under per-entity refs, readers fold), git-annex (per-repository branches), and git
  scraping (git history as a time index, one writer per file).
