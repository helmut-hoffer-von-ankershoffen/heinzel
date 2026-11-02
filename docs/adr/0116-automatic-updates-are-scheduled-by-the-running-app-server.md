---
status: proposed
date: 2026-11-02
implementation: built
decision-makers: maintainer
related: 0031, 0047, 0083, 0085, 0114
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
  on a restartable `hz ui`, on an installation the web update supports.
- **Development is judged by facts, not a setting.** The server never checks, schedules or applies, and shows no
  popup, when any of these holds at start: the `heinzel` package is imported from outside `site-packages` (a source
  tree) or its distribution is an editable install; the environment's folder sits in a checkout (a `.git` or
  `pyproject.toml` next to the `.venv`); `HZ_DEV`, `HZ_TEST`, `CI`, `PYTEST_CURRENT_TEST`, `PYTEST_XDIST_WORKER`,
  `TEST_WORKER_INDEX` or `PLAYWRIGHT_TEST_BASE_URL` is set; make (`MAKELEVEL`) or an npm script such as the Vite dev
  server (`npm_lifecycle_event`) started it; or the state folder isn't the user's default. Both switches are then
  disabled and say "Updates are managed by your source checkout." (or, for the other facts, that hz runs for
  development or tests). An installed wheel or uv tool install with the default state folder keeps the default on.
  `HEINZEL_AUTO_UPDATE_TEST=1` counts none of the facts; only the end-to-end test of the real flow sets it.
- The server asks for a newer stable hz release and newer releases of installed plugins every 30 minutes, with up to
  5 minutes of random jitter (the first check 1 to 5 minutes after start).
- When something is newer and nothing is busy (no request that changes state is in flight, no daemon job holds the
  tick lock), it opens a 30-second countdown. `GET /api/auto-update` tells every window the plan and the seconds
  left; `POST /api/auto-update/postpone` moves the apply time 1 hour later, as often as wanted. Work that starts
  during the countdown cancels it until things are idle again.
- At the apply time it runs the existing `system.update` operation (hz and plugins together). If anything changed,
  it first starts the newly installed hz as a trial (`hz ui` on a free port, its own token, automatic updates off) and
  waits up to 90 seconds for `/api/lifecycle` to answer with the new version. Only then does it restart in place.
  Windows reconnect without signing in and reload once the version changed.
- **Rollback.** If the trial start fails, the server never restarts into the new version: it installs the version it
  runs again from its release tag (`system.update` with that source), keeps running, and shows one plain message
  ("hz X was installed but didn't start, so hz Y was put back and keeps running."). Installed from a release tag, it
  tries again 6 hours later. Installed from the package index, it puts back exactly `heinzel==Y`, which holds the
  installation there: automatic updates stop (a `==` index pin counts as pinned) until a person runs
  `hz system update --source heinzel`, so a version that doesn't start is never retried in a loop. An edge wheel or a
  branch has no version to name: the old process keeps running and the message says how to go back. If the version
  loaded after a restart is still the old one, the plan records the failure.
- The plan (what is pending, the apply time, postponements, the last failure and its message) lives in the server
  and is saved under the state folder, so it survives a restart.
- If the update fails, the installer's rollback keeps the old version running; the server shows one plain message
  and tries again no sooner than 6 hours later.

### Consequences

- Good: one owner of the plan; the browser only shows it.
- Good: no new installer, restart or verification path.
- Bad: nothing updates while `hz ui` is not running; the launchd daemon alone does not update.
- Good: on by default, yet a developer's checkout, a test run or CI never updates itself.
- Bad: a version that passes the trial start but fails later in the restart itself (the trial ran the same command)
  has no automatic way back; the old server has already handed over. The trial makes this unlikely.
- Bad: after a failed trial an installation from the package index stays held at the old version until a person
  updates; an edge wheel or a branch can't be put back by itself and keeps the old process running until a restart.

### Confirmation

Unit tests of the scheduler fail when a countdown opens while busy, a postpone does not move the apply time by an
hour, a failure retries sooner than 6 hours, a failed trial start restarts or doesn't reinstall the old version, the
default is off, any one development fact leaves updates on, or a faked installed run counts as development. Vitest
covers both switches, live save and the disabled state with its line. An end-to-end test with a fake newer release,
run with `HEINZEL_AUTO_UPDATE_TEST=1`, checks in a real browser that both switches show each other's change, then the
countdown, postpone, trial start and apply.

## More Information

Implementing pull requests:

| Pull request | What it builds |
| --- | --- |
| #452 | setting (on by default), switches, development facts, scheduler, trial start and rollback, endpoints, popup, tests |
