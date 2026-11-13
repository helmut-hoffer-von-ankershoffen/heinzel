---
status: proposed
date: 2026-11-13
implementation: planned
decision-makers: owner
amends: 0126, 0020
amends-0126: reminders run in the daemon's tick, under the daemon's consent; no separate reminder job
amends-0020: a tick may stay for a bounded reminder lane until just before the next slot
related: 0078, 0133
---

# 0147: Calendar reminders run inside the daemon, under the daemon's consent

## Context and Problem Statement

[ADR 0126](0126-calendar-reminders.md) gives calendar reminders their own user job. That job is a separate
per-profile launchd or systemd unit that the person installs through its own confirmation, besides turning on
Calendar reminders. In use this produced two background things on one computer. The daemon pane said "Daemon
running", while Calendar said "Reminders need a background worker on this computer." People read that as a broken
daemon, not as a second job they had never installed. Nothing set the job up on first run or after an update.

0126 kept reminders out of the daemon for one reason: "A five-minute tick is not a five-minute warning". An event may
start between ticks, and a tick that exits right away cannot wake at a deadline in between. How can reminders run
inside the daemon, under the daemon's existing consent, and still meet that deadline?

## Decision Drivers

- One background thing per computer. The person agrees to it once
  ([ADR 0078](0078-native-desktop-uses-the-shared-lifecycle-and-daemon.md) point 3: "Keep my Heinzelmännchen working
  while the app is closed").
- Reminders keep 0126's deadline: a provider refresh at least every 30 seconds, then a wake at the next boundary.
- Updates still reach every process. Nothing long-lived keeps running replaced code
  ([ADR 0020](0020-daemon-head.md)).
- Calendar reads keep their own consent: the Calendar reminders notification setting.
- Ownership, maintenance and removal stay exact ([ADR 0133](0133-owned-host-integrations-join-maintenance.md)).

## Considered Options

- **A bounded reminder lane inside each tick, until just before the next slot** (chosen).
- **The daemon installs and owns the existing reminder unit.** This is still a second job to show, repair and
  migrate, with one more launchd label per profile. The owner asked for no separate reminder job.
- **The daemon rewrites its own unit to a one-shot wake at the next reminder.** Every meeting would cause a launchd
  or systemd reload. A failed rewrite silently loses reminders, and it races updates and pauses.
- **Resident daemon mode (`KeepAlive`) for everyone with reminders.** ADR 0020 rejects resident mode as the default:
  the process keeps running replaced code, and it must handle sleep itself.
- **Shorter ticks (every minute).** Still not a deadline, and every tick boots the whole kernel.

## Decision Outcome

Chosen option: **a bounded reminder lane inside each tick**, because it keeps one supervised job, 0126's deadline and
ADR 0020's exit-on-every-slot property.

1. **The lane.** When Calendar reminders are on for the profile, `hz system daemon tick` starts the reminder lane
   first. Its jobs then run beside the lane. The lane is 0126's existing cycle: refresh at least every 30 seconds,
   wake at the next boundary, revalidate before delivery. It ends at the next daemon slot minus a margin (10 seconds
   by default). The tick exits after both its jobs and the lane have ended. With reminders off, a tick exits as it
   does today.
2. **Locks.** The lane holds no tick lock. A manual tick, `pause`, `hz system update` and uninstall do not wait for
   it. Each lane cycle enters 0126's lifecycle fence (SDK-wide maintenance gate plus the profile lock) and ends the
   lane when maintenance is pending.
3. **Why the slot matters.** launchd's `StartCalendarInterval` and systemd's timer do not start a second instance
   while the first one runs. A lane that ran past its slot would therefore drop the next tick. The margin keeps the
   lane ending before the slot. A tick that still overruns is a daemon health finding, never a silent skip.
4. **Coverage and sleep.** Between the lane's end and the next tick's lane there is a gap of the margin plus process
   start (seconds), well inside the 30-second refresh. After sleep, the slot that launchd or systemd catches up
   starts a fresh lane. 0126's rules still hold: expiry is exclusive at the meeting boundary, and a resumed machine
   never alerts after its boundary.
5. **Consent.** Running the lane needs the daemon (its existing consent, ADR 0078 point 3) and Calendar reminders
   on in Notification settings (calendar-read consent, still user-scoped and off by default). There is no third
   step and no separate install. A paused daemon pauses reminders, and Calendar says so.
6. **Status.** `org.calendar-reminders-status` reports the daemon's installed, loaded and paused state, plus the
   lane's last heartbeat (written under the profile's state directory). Calendar shows a line only when reminders
   are on but cannot run (no daemon, daemon paused, lane overdue). That line comes with one action: the daemon's own
   setup or resume. The daemon pane lists "Calendar reminders" with the lane state.
7. **Migration.** The first tick that runs a lane looks for 0126's per-profile unit through the 0133 inventory. If
   that unit byte-matches the definition for this profile, the tick boots it out and removes it. Duplicate alerts
   are impossible anyway, because delivery is deduplicated by replay key. A modified, linked or foreign unit stays
   untouched and becomes a doctor finding with its removal action. The `calendar-reminders-install` and `-uninstall`
   operations and the separate confirmation are withdrawn. `calendar-reminder-worker` is kept for one release, so a
   unit that is still loaded exits cleanly.
8. **Interval settings.** A daemon interval longer than five minutes changes nothing for reminders: each lane covers
   its whole interval. The cost is one Python process alive while reminders are on, as with 0126's worker. It now
   restarts at every slot instead of every 300 seconds.

### Consequences

- Good: one background job, one consent, one status. Reminders start whenever the daemon runs and Calendar reminders
  are on, after first run and after every update, with no Calendar setup step.
- Good: a tick still exits at every slot, so new code runs from the next slot on.
- Bad: with reminders on, the daemon is effectively always present. Its memory is visible in the daemon pane, as the
  separate worker's was before.
- Bad: correctness now depends on the slot margin and on launchd and systemd skipping overlapping runs. Both must be
  observed on real OS managers, not only in fakes.

### Confirmation

- Unit tests with fake clocks and supervisors: the lane ends before the next slot, holds no tick lock, stops at a
  maintenance gate, never alerts after a boundary across simulated sleep, and the migration removes only a
  byte-matching unit.
- An isolated-HOME lifecycle test on macOS (launchd) and Linux (systemd user): a reminder is delivered within the
  deadline across three consecutive slots, and an update in the middle of a lane switches the code at the next slot.
- Web tests: no Calendar notice while the daemon runs with reminders on, and one action when the daemon is missing or
  paused.

## More Information

This ADR replaces three parts of 0126: "separately installs a user service", "no long-lived process is installed
automatically" (the daemon is the installed process), and the desktop head's separate setup confirmation. 0126's
deadline, consent, expiry, replay and profile-binding rules are unchanged.
