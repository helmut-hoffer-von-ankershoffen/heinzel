---
status: proposed
date: 2026-11-02
implementation: built
decision-makers: maintainer
related: 0006, 0031, 0047, 0083, 0085, 0114
implemented-by: 452
---

# 0116: Automatic updates are scheduled by the running app server

## Context and Problem Statement

Updating today needs a person: the CLI notice (ADR 0031) and the footer's update pane (ADR 0085) say a release
exists, and someone clicks Update. The owner asked for updates that happen by themselves, on by default, with a short
warning and a way to put them off, and never while someone develops hz. Who decides when to update, where does that state live, and what happens when nobody is
watching or something is running?

## Decision Drivers

- On by default, also for existing installations after they upgrade; stable releases only.
- Never while hz is being developed or tested: an update would replace code under work or race a test.
- Two windows, a reload and no window at all must all see the same plan.
- Never cut off a chat turn or a running helper job.
- A failed update must leave the old version installed and running, say so once, and not retry in a loop.

## Considered Options

- **The `hz ui` server owns the schedule and applies with the existing installer and restart** (chosen)
- **The browser owns the schedule**: each window would keep its own countdown, a reload would lose it, and nothing
  would update with no window open.
- **The launchd daemon tick applies updates**: it can't restart the app server in place on the same port, and it
  ticks only every few minutes, too coarse for a 30-second countdown.

## Decision Outcome

Chosen option: **the `hz ui` server owns the schedule**, because it already serves every window, can restart itself in
place (ADR 0047, 0083) and runs whether or not a window is open.

- The kernel setting `auto_update` ("Automatic updates", default **on**, user-level only) turns it off and on. Two
  switches edit it, both live (no restart): one in the footer's Updates pane and the setting's row in Settings →
  Updates, found there by search as well; each says in one line what it does, and a change in one shows in the other.
  `hz system config set hz.auto_update false` and `HZ_AUTO_UPDATE=false` work too. It runs only on the stable channel,
  on a restartable `hz ui`, on an installation the web update supports, and never while `update_check` is off: then
  nothing is asked at all, and both switches are disabled and say that update checks are off.
- **Development is judged by facts, not a setting.** The server never checks, schedules or applies, and shows no
  popup, when any of these holds at start: the `heinzel` package is imported from outside `site-packages` (a source
  tree) or its distribution is an editable install; the environment's folder sits in a checkout (a `.git` or
  `pyproject.toml` next to the `.venv`); `HZ_DEV`, `HZ_TEST`, `CI`, `PYTEST_CURRENT_TEST`, `PYTEST_XDIST_WORKER`,
  `TEST_WORKER_INDEX` or `PLAYWRIGHT_TEST_BASE_URL` is set; make (`MAKELEVEL`) or an npm script such as the Vite dev
  server (`npm_lifecycle_event`) started it; or the state folder isn't the user's default. Both switches are then
  disabled and say "Updates are managed by your source checkout." (or, for the other facts, that hz runs for
  development or tests). An installed wheel or uv tool install with the default state folder keeps the default on.
  `HEINZEL_AUTO_UPDATE_TEST=1` counts none of the facts; only the end-to-end test of the real flow sets it.
- The server asks for a newer stable hz release (GitHub) and newer releases of installed plugins (PyPI) every 30
  minutes, with up to 5 minutes of random jitter (the first check 1 to 5 minutes after start).
- **Only old enough, stable, unyanked releases are announced.** hz counts once its GitHub release is
  `auto_update_min_age_hours` old (default 6), a plugin once its first PyPI upload is
  `auto_update_plugin_min_age_hours` old (default 24), so a broken or hijacked release can be withdrawn first; an
  unknown publish time counts as too new. Pre-releases, drafts and yanked plugin releases never count. The commit
  that hz's tag names is recorded when hz is announced. `hz system update` on the stable channel never moves onto a
  pre-release either, not even from one (this amends ADR 0006).
- When something is newer and nothing is busy (no request that changes state is in flight, no daemon job holds the
  tick lock), it opens a 30-second countdown. A request is in flight until its response body has been sent or
  dropped, so a streamed chat turn holds it off while it streams, and a streamed operation holds it in its own thread
  while it runs on after the browser left. `GET /api/auto-update` tells every window the plan and the seconds
  left; `POST /api/auto-update/postpone` moves the apply time 1 hour later, as often as wanted. Work that starts
  during the countdown cancels it until things are idle again.
- At the apply time it first copies the whole uv tool environment (hz, every plugin, their dependencies and the
  receipt) under the state folder; without a copy nothing is installed. It then runs the existing `system.update`
  operation (hz and plugins together) and checks that the environment holds **exactly the announced versions**: hz
  and each announced plugin at the announced version, every other plugin unchanged, and hz from git at the commit
  its tag named when it was announced. Anything else is put back from the copy. If that holds, it starts the newly
  installed hz as a trial: `hz ui --port 0` with its own token, on a throwaway copy of the configuration and empty
  state, data, cache and log folders, with update checks off, deleted afterwards, so the live folders are never
  touched. It waits up to 90 seconds for `/api/lifecycle` to answer with the new version. Only then does it restart
  in place. Windows reconnect without signing in and reload once the version changed.
- **Rollback.** If the install fails, installs other versions than announced, or the trial start fails, the server
  never restarts: it puts the copy back at the same path, so hz and every plugin are exactly as they ran, keeps
  running, and shows one plain message ("hz X was installed but didn't start, so hz Y and its plugins (…) were put
  back as they were and keep running."). An update whose trial failed is never tried again; a newer one is. Other
  failures try again no sooner than 6 hours later. If putting back fails, the message says how to reinstall.
- The plan (what is pending, the apply time, postponements, the last failure and its message, a refused update)
  lives in the server and is saved under the state folder, so it survives a restart.
- **Two servers on one state folder.** Every change to the plan reads the state file, changes it and writes it back
  under an exclusive `flock` (`auto-update.lock`), so one server's postpone and another's dismissal both stay; no
  last writer wins. Applying takes a second lock (`auto-update-apply.lock`) held until the process is replaced; a
  server that can't take it leaves the update to the other and looks again an hour later.

### Consequences

- Good: one owner of the plan; the browser only shows it.
- Good: no new installer or restart path; the copy and the exact-version check are the only new safety steps.
- Bad: nothing updates while `hz ui` is not running; the launchd daemon alone does not update.
- Good: on by default, yet a developer's checkout, a test run or CI never updates itself.
- Bad: a version that passes the trial start but fails later in the restart itself (the trial ran the same command)
  has no automatic way back; the old server has already handed over. The trial makes this unlikely.
- Bad: the copy of the environment costs disk space and a few seconds per update.
- Bad: PyPI's file hashes are not passed to uv as `--require-hashes`: a uv tool install resolves dependencies anew
  and uv takes required hashes only for every requirement of a fully pinned set. uv still checks the hashes the
  index serves for each file it downloads; the minimum age and the exact-version check are the defence here.
- Bad: a second server on the same state folder that didn't apply keeps running the old version until it restarts.

### Confirmation

Unit tests of the scheduler fail when a countdown opens while busy, a postpone does not move the apply time by an
hour, a failure retries sooner than 6 hours, a failed trial start restarts or doesn't put hz and every plugin back,
the trial start writes to a live folder, the default is off, update checks off still checks, a release younger than
its minimum age, a pre-release or one of unknown age is announced, other versions than announced or a moved tag are
kept, two servers lose each other's changes or both apply, any one development fact leaves updates on, or a faked
installed run counts as development. An integration test fails when a streamed turn stops counting as work before
its stream or its operation has ended. Vitest
covers both switches, live save and the disabled state with its line. An end-to-end test with a fake newer release,
run with `HEINZEL_AUTO_UPDATE_TEST=1`, checks in a real browser that both switches show each other's change, then the
countdown, postpone, trial start and apply.

## More Information

Implementing pull requests:

| Pull request | What it builds |
| --- | --- |
| #452 | setting (on by default), switches, development facts, scheduler, minimum age, exact versions, trial start, environment copy and rollback, locks, endpoints, popup, tests |
