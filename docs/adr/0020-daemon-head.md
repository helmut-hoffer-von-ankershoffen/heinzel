---
status: proposed
date: 2026-10-23
amends: 0010
---

# 0020: Add a daemon head for background and reactive operation

## Context and Problem Statement

`hz` must serve people interactively, agents through the CLI and MCP ([ADR 0019](0019-mcp-and-rest-heads.md)), and
also run **in the background on macOS and Linux**:

- on schedules, such as learning feeds that turn into newsletters;
- in reaction to changes in GitHub, Google Drive, Atlassian tools and feeds;
- following workflows that teams write inside their own artefacts (a "🧝 Heinzel" tab in a Doc or Sheet).

Many people's daemons must cooperate rather than collide, with no server of our own. Nothing may be published without
approval (tenet 3, non-goals), and every run must leave a receipt with lineage (tenet 4).
[ADR 0010](0010-towards-a-reactive-workflow-engine.md) proposed borrowing Reactor's contracts, fingerprints and
receipts and named a daemon head.

## Decision Drivers

- `hz system update` replaces the uv tool environment (`uv tool upgrade` / `uv tool install --force`). A long-lived
  Python process would keep running code that is no longer on disk.
- Laptops sleep. launchd's `StartCalendarInterval` and systemd's `OnCalendar=` with `Persistent=true` catch up
  missed runs, coalesced, after wake. `StartInterval` and in-process timers do not.
- Laptops sit behind NAT. Webhooks (GitHub, Drive push channels, Jira) need a public HTTPS endpoint, while
  conditional polling is cheap: a GitHub 304 does not count against the primary rate limit.
- GitHub is already shared by every team. A non-fast-forward push is rejected, which gives compare-and-swap without
  a server.
- Plugins are in-process Python with the user's privileges, and artefact text can be edited by anyone with access.

## Considered Options

- **OS-supervised short-lived ticks (launchd / systemd), team state and leases in a GitHub state repository**
  (chosen)
- **Self-daemonizing process (fork, pidfile):** no start at login, no restart, no catch-up. It reinvents supervision.
- **Resident process under `KeepAlive` as the default:** keeps running replaced code after `hz system update`, must
  handle wall-clock deadlines across sleep itself, and its memory grows. Kept as an opt-in mode.
- **cron:** skips runs during sleep on macOS; no logs, limits or unit management.
- **GitHub Actions only:** no access to personal credentials or local files; schedules of at least 5 minutes that can
  be delayed or dropped. Kept as the team runner.
- **A hosted GitHub App or Probot service with webhooks:** needs an always-on public endpoint and operations work,
  against "open and independent" for a first version. Possible later for orgs.
- **Push subscriptions on laptops (tunnels, relays):** an extra public attack surface, channel renewal, and delivery
  to sleeping machines.
- **Leases as files through the Contents API, or as issue labels:** files share the receipts branch and conflict
  under parallel calls; labels are not atomic.
- **Reactor as a dependency:** alpha and TypeScript; we borrow its concepts and keep a compatible contract dialect
  (ADR 0010).
- **An in-process Python scheduler as the daemon (APScheduler, Celery beat, Huey, Rocketry, `schedule`):** they
  compute schedules well, but only while a Python process stays up, so the question they leave open is supervision.
  Something must still start the process at login and restart it after a crash (launchd or systemd anyway); the
  process keeps running replaced code after `hz system update`; missed runs are only handled while it is alive
  (APScheduler's `coalesce` and `misfire_grace_time`); and APScheduler 4's multi-node coordination needs a shared
  database and event broker, the server infrastructure the GitHub state repository avoids. We use a library for the
  schedule arithmetic (cron expressions, next and missed fire times: `croniter` or APScheduler's `CronTrigger`,
  chosen by maintenance health when implemented), and APScheduler 4's `AsyncScheduler` for the opt-in resident mode
  rather than a hand-written loop.

## Decision Outcome

Chosen option (proposed): **the OS supervises, the daemon ticks; GitHub holds shared state, leases and approvals**,
because one short-lived `tick()` survives updates, crashes and sleep, and many daemons cooperate without a server of
our own.

1. **Process model: the OS supervises, the daemon ticks.**
   - `hz system daemon install` writes a launchd LaunchAgent (`~/Library/LaunchAgents`, `StartCalendarInterval`,
     `ProcessType=Background`, `LowPriorityIO`, `Nice`) or a systemd user service plus timer
     (`~/.config/systemd/user`, `OnCalendar=`, `Persistent=true`, `RandomizedDelaySec=`). It points at the stable
     uv tool shim (`uv tool dir --bin`), never at the environment's interpreter. The default interval is 5 minutes.
   - Each firing runs `hz system daemon tick`, a short-lived process: boot, reconcile, write receipts, exit.
   - `hz system serve daemon` runs the same `tick()` in a foreground loop. An opt-in `--resident` mode
     (`KeepAlive` / `Restart=always`) for file watching comes later.
   - Commands live under `hz system daemon` (ADR 0018) as ordinary operations: `install`, `uninstall`, `status`,
     `logs`, `tick`, `pause`, `resume`, `jobs`. `status` is also a `HealthLet`.
   - `hz system update` takes the local tick lock and restarts resident mode.
2. **Two new let kinds; contracts compile to them.**
   - `WatchLet`: name, provider (injected), period, scope, source id, canonicalizer. It returns facets that the kernel
     fingerprints (SHA-256 of canonical JSON) and gets an injected `Cursor` (ETag or page token).
   - `JobLet`: name, func (injected like an operation, never prints), summary, `schedule` (cron), `on` (watch facets),
     `scope` (`personal` | `team`), `publishes` (action types that need approval), `timeout`, `retries`,
     `concurrency`, `catch_up` (`latest` | `all` | `skip`), `idempotency` (defaults to the memo key), owner.
   - `ContractLet` (ADR 0010) is compiled at boot into a `JobLet` whose func runs an agent session.
   - Publishing is an ordinary `OperationLet` with `read_only=False`, so every head exposes it.
3. **The daemon head consumes lets like the other heads.** `heinzel.heads.daemon` is imported only when served. It
   reads `kernel.lets(JobLet)` and `kernel.lets(WatchLet)`, respects `kernel.available()`, and invokes through kernel
   injection. It reaches shared state only **by type**: the kernel defines `Ledger`, `Leases` and `Inbox`
   protocols, and a `state` module provides them. With `state` disabled, local implementations under platformdirs
   apply. This keeps the rule that heads import neither modules nor anything the kernel did not discover.
4. **Reconciliation follows Reactor.**
   - Clocks only wake jobs. A job runs when its memo key `(contract fingerprint, input facet fingerprints[, freshness
     epoch])` differs from the last rendered receipt; otherwise the disposition is `skipped`.
   - Missed schedule slots are computed from the **shared ledger**, not local memory.
   - Each job runs in a subprocess with its timeout.
   - Only the leaseholder of a source polls it, with conditional requests, `retry-after` and exponential backoff.
5. **Team state lives in a GitHub state repository.**
   - Receipts are append-only, one file per receipt, never edited; one commit per tick, rebased and retried on
     rejection.
   - Leases are refs `refs/heads/hz/lease/<job-or-source>` whose acquire, renew and release commits form a
     fast-forward chain. A rejected push is the compare-and-swap. Leases carry an expiry and a fencing token that
     receipts record.
   - The repository also holds `control.toml` (kill switch, disabled jobs, cost ceilings),
     `policies/auto-approve.toml` (owned) and `workflows/registry.toml`.
6. **Two runners, one ledger.** `scope=personal` jobs run only on their owner's daemon. `scope=team` jobs run in a
   scheduled GitHub Actions workflow (`hz system daemon tick --scope team`, one `concurrency` group) when the team
   configures one; otherwise they are leased among daemons.
7. **Approvals live in GitHub.**
   - Every proposal becomes an issue in the state repository, or a pull request for file-like drafts.
   - `hz inbox list|show|approve|reject` are operations, so CLI, MCP and REST share one inbox.
   - Auto-approval is a policy entry with an owner, an action type, a scope and an expiry, changed by pull request;
     receipts name it.
8. **Receipts** are JSON, content-addressed and hash-chained per job, with:
   - job, contract and input fingerprints, memo key;
   - wake cause; disposition (`rendered|skipped|failed|awaiting_approval|approved|rejected|published`);
   - runner (person, host, versions, laptop or actions), lease token, idempotency key;
   - draft, approval and publish references, sources, cost, `as_of`.

   `skipped` receipts stay local by default.
9. **Untrusted input is data until registered.** Workflows found in artefacts (a "🧝 Heinzel" tab) only produce a
   registration proposal. They run once registered with an owner and a pinned fingerprint, and pause for approval
   when the text changes. Renders get read-only tools; writes happen only through approved publish operations.
   Unattended runs are limited to an allowlist of modules.
10. **Operations hygiene.**
    - Logs are rotated JSON lines in `platformdirs.user_log_dir`.
    - Secrets come from the OS keychain through `keyring`, never from unit files.
    - Kill switches (local flag, team `control.toml`, per job) are checked before every tick and every publish.
    - `--dry-run` on `install` and `tick`.

### Consequences

- One `tick()` serves launchd, systemd, Actions and the foreground loop. Updates, crashes and sleep are handled by
  construction, and tests can drive it with a fake clock.
- Latency is one tick (minutes) unless push lands in Actions or resident mode is on. That is acceptable for drafting
  busywork.
- Teams need a state repository and tokens with write access to it. `doctor` must check both, along with the
  launchd Login Items state and a stale shim path.
- New work: the `state` module and kernel protocols, the two lets, the daemon head, `hz inbox`, an Actions workflow
  template, plist and unit writers with golden-file tests.
- Amends ADR 0010: "one per person, with a lease and a hot standby" becomes "every daemon ticks; leases per job or
  source decide who works; expired leases are taken over at the next tick".
- Plugin code still runs with user privileges. Per-job OS sandboxing (Seatbelt, bubblewrap) is deferred and needs
  its own ADR.

### Confirmation

Partly, for the local part built in 0.2 (see the implementation status under More Information): golden-file tests for the plist and units
(`tests/unit/modules/daemon_units_test.py`), `tick()` driven by a fake clock
(`tests/unit/modules/daemon_service_test.py`) and the CLI and foreground loop (`tests/integration/daemon_test.py`).
Still planned: `doctor` checks for the state repository, tokens, the launchd Login Items state and a stale shim path,
and tests of two daemons plus Actions against one state repository.

## More Information

- Amends the "always-on runner" row of [ADR 0010](0010-towards-a-reactive-workflow-engine.md); see Consequences.
- Research, sources and the phased plan: [docs/research/daemon-head.md](../research/daemon-head.md).
- Implementation starts after 0.1.0.

**Implementation status.** The status stays **proposed** until the whole decision is built; 0.2 builds its local,
personal part.

**Built in 0.2 (the daemon runs locally):**

- `JobLet` and `@module.job(schedule=..., timeout=..., retries=..., catch_up=..., idempotency=...)` in the public
  API, with `scope="personal"` only. The `system.daemon` submodule consumes it: `install` (launchd LaunchAgent or
  systemd user service and timer, pointing at the shim from `uv tool dir --bin`; `--interval`, `--dry-run`),
  `uninstall`, `status` (also a `HealthLet`), `logs`, `tick` (`--dry-run`, `--job`), `pause`, `resume`, `jobs`.
  `hz system serve daemon` is the foreground loop. Golden-file tests cover the plist and the units; a fake clock
  drives `tick`.
- Where the code lives: the reconciliation is in the `system.daemon` service, because `tick` must be an ordinary
  operation in every head and modules can't import heads. `heinzel.heads.daemon` finds the `tick` operation through
  the kernel and invokes it in a loop, so heads still import no module. When the `Ledger` protocol moves into the
  kernel (0.3), the reconciliation can follow it.
- A local ledger replaces the shared one: one JSON file per receipt in `<data_dir>/receipts/`, hash-chained per job
  (`id` is the SHA-256 of the canonical JSON without `id`; `prev` names the job's previous receipt), with `job`,
  `wake` (`schedule|manual`), `disposition` (`rendered|skipped|failed`), `as_of` (the slot), `idempotency_key` and
  `error`. A changed receipt or a broken chain stops the tick with a message rather than being repaired silently.
- Slots and catch-up. Missed slots are computed from the job's last `schedule` receipt. A job without one starts at
  the previous tick, or one interval ago on the first tick, so a new job never replays its history. `latest` runs the
  newest missed slot, `all` every missed slot (at most 100 per tick, oldest first), and `skip` only a slot that fell
  within the last interval, recording a missed one as `skipped`. A failed slot is consumed; its retries happen within
  the tick. A slot whose idempotency key (default `<job>@<slot>`) was rendered is `skipped`.
- Intervals divide an hour (1 to 30 minutes, or 60), so launchd's `Minute` list and systemd's `OnCalendar=*:0/N`
  fire on the same slots.
- Operations hygiene: a local tick lock (`flock`) and pause flag under `<state_dir>/daemon/`, a JSON-lines log in
  `<log_dir>/daemon.jsonl` rotated by size, and a timeout per job, in process: an overrunning job is abandoned in a
  daemon thread so the tick still exits.
- Schedule arithmetic: **croniter**, not APScheduler's `CronTrigger`. Checked on 2026-10-24: croniter 6.2.4 was
  released on 2026-07-10 (6.1 to 6.2.4 since March 2026), maintained by the pallets-eco organisation, with its
  repository pushed to on 2026-10-23 and 18 open issues; it depends only on python-dateutil and has typeshed stubs
  (`types-croniter`). APScheduler's 3.x line gets maintenance releases (3.11.3 on 2026-06-28) while 4.0 has been
  an alpha since 2025-04 (4.0.0a6), its repository has 74 open issues, and it brings tzlocal and a scheduler we don't
  use. croniter's `get_next` and `get_prev` are exactly the "slots since the last receipt" question.

**Remaining:** the `state` module with `Ledger`, `Leases` and `Inbox` protocols and the shared ledger in a GitHub
state repository; lease refs; `WatchLet` and conditional polling; `scope="team"`, `publishes`, `concurrency`,
owners and the Actions runner; approvals and `hz inbox`; memo keys and fingerprints; per-job subprocesses with
their timeout; the resident mode; the team kill switch; `doctor` checks for a stale shim path and the Login Items
state; and `hz system update` taking the tick lock and restarting resident mode.
