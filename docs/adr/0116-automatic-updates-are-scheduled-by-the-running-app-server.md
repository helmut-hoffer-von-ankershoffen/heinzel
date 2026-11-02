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
exists, and someone clicks Update. The owner asked for updates that happen by themselves, with a short warning and a
way to put them off. Who decides when to update, where does that state live, and what happens when nobody is
watching or something is running?

## Decision Drivers

- Off until the owner turns it on; stable releases only.
- Two windows, a reload and no window at all must all see the same plan.
- Never cut off a chat turn or a running helper job.
- A failed update must leave the old version running, say so once, and not retry in a loop.

## Considered Options

- **The `hz ui` server owns the schedule and applies with the existing installer and restart** (chosen)
- **The browser owns the schedule**: each window would keep its own countdown, a reload would lose it, and nothing
  would update with no window open.
- **The launchd daemon tick applies updates**: it can't restart the app server in place on the same port, and it
  ticks only every few minutes, too coarse for a 30-second countdown.

## Decision Outcome

Chosen option: **the `hz ui` server owns the schedule**, because it already serves every window, can restart itself in
place (ADR 0047, 0083) and runs whether or not a window is open.

- The kernel setting `auto_update` ("Automatic updates", default off, user-level only) turns it on, in Settings or
  with `hz system config set hz.auto_update true`. It runs only on the stable channel, on a restartable `hz ui`, on an
  installation the web update supports.
- The server asks for a newer stable hz release and newer releases of installed plugins every 30 minutes, with up to
  5 minutes of random jitter (the first check 1 to 5 minutes after start).
- When something is newer and nothing is busy (no request that changes state is in flight, no daemon job holds the
  tick lock), it opens a 30-second countdown. `GET /api/auto-update` tells every window the plan and the seconds
  left; `POST /api/auto-update/postpone` moves the apply time 1 hour later, as often as wanted. Work that starts
  during the countdown cancels it until things are idle again.
- At the apply time it runs the existing `system.update` operation (hz and plugins together) and, if anything
  changed, the existing restart in place. Windows reconnect without signing in and reload once the version changed.
- The plan (what is pending, the apply time, postponements, the last failure and its message) lives in the server
  and is saved under the state folder, so it survives a restart.
- If the update fails, the installer's rollback keeps the old version running; the server shows one plain message
  and tries again no sooner than 6 hours later.

### Consequences

- Good: one owner of the plan; the browser only shows it.
- Good: no new installer, restart or verification path.
- Bad: nothing updates while `hz ui` is not running; the launchd daemon alone does not update.
- Bad: if the new version cannot start after the restart, there is no automatic way back; this is the same risk as a
  manual update today.

### Confirmation

Unit tests of the scheduler fail when a countdown opens while busy, a postpone does not move the apply time by an
hour, or a failure retries sooner than 6 hours. An end-to-end test with a fake newer release checks the countdown,
postpone and apply in a real browser.

## More Information

Implementing pull requests:

| Pull request | What it builds |
| --- | --- |
| #452 | setting, scheduler, endpoints, popup, unit and end-to-end tests |
