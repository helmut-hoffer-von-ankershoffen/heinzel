---
status: proposed
date: 2026-11-13
implementation: planned
decision-makers: owner
amends: 0126, 0020, 0133
amends-0126: reminders run in a lane of the scheduled daemon tick, under the daemon's consent; no separate install
amends-0020: a scheduled tick may keep a bounded reminder lane until a slot window; Linux timer settings
amends-0133: the calendar-worker inventory becomes migration and doctor only
related: 0078
---

# 0147: Calendar reminders run inside the daemon, under the daemon's consent

## Context and Problem Statement

[ADR 0126](0126-calendar-reminders.md) gives calendar reminders their own job: a per-profile launchd or systemd unit
with `KeepAlive`. The person installs it with a confirmation of its own, in addition to turning on Calendar
reminders. In use, one computer then had two background things. The daemon pane said "Daemon running" while Calendar
said "Reminders need a background worker on this computer". Nothing set up the second job on first run or after an
update.

0126 kept reminders out of the daemon for one reason: "A five-minute tick is not a five-minute warning". The question
here is how reminders can run inside the daemon, under its existing consent, and still meet that deadline. That
includes long jobs, sleep, clock steps, timer jitter and updates.

## Decision Drivers

- One background job and one consent per computer
  ([ADR 0078](0078-native-desktop-uses-the-shared-lifecycle-and-daemon.md) point 3).
- 0126's deadline holds. The provider is refreshed at least every 30 seconds, and a reminder is delivered at its due
  instant, not only somewhere within the refresh period.
- Coverage survives jobs longer than an interval, sleep and wake, clock steps and updates.
- New code is picked up at slots. Nothing runs replaced code for longer than an interval ([ADR 0020](0020-daemon-head.md)).
- Calendar reads keep their own consent, the user-scoped Calendar reminders setting, and the person is told what it
  starts.
- Ownership, maintenance and removal stay exact ([ADR 0133](0133-owned-host-integrations-join-maintenance.md)).

## Considered Options

- **A reminder lane, as a child process of the scheduled tick, that ends only inside a slot window** (chosen).
- **The same lane in the tick's own process.** This has one process fewer, but a hung job thread, a native crash or a
  memory spike in a plugin job takes reminders down with it. The job's memory also stays resident for the rest of
  the interval.
- **A self-restarting resident daemon.** The daemon unit gets `KeepAlive` (or `Restart=always`), runs due slots from
  the ledger and exits at every slot boundary, so the manager restarts it with current code.
  - For: it answers 0020's two objections to resident mode. Replaced code lives at most one slot, and missed slots
    come from the ledger (0020 decision 4), not from process memory. It needs no margin, does not depend on the
    manager dropping overlapping firings, and has no gap after wake.
  - Against: it changes the daemon unit for everyone. One process stays resident even with reminders off, unless the
    unit switches form with the setting, which means a reload on every toggle. It also replaces the catch-up by the
    manager, which 0020 chose deliberately.
  - This option is kept as the fallback if the lifecycle tests below show that dropped overlapping firings are not
    reliable on a supported manager.
- **The daemon installs and owns the existing 0126 unit.** That is still a second label to show, repair and migrate,
  and the owner asked for no separate reminder job.
- **A one-shot job per upcoming reminder.** On macOS this needs a second label, plus a plist rewrite and bootstrap for
  every meeting. `systemd-run --on-calendar` would be easy on Linux but has no macOS counterpart.
- **Shorter ticks, or a resident daemon by default.** Shorter ticks are still no deadline, and every one boots the
  whole kernel. Resident by default was rejected by 0020.

## Decision Outcome

Chosen option: **a child-process reminder lane of the scheduled tick that ends only inside a slot window**. It keeps
0020's unit, the catch-up by the manager and the exit at slots, adds isolation from jobs, and meets 0126's deadline.

1. **Who runs a lane.** A lane runs only in a manager-bound tick (`_service_manager_pid() == os.getpid()`), when
   Calendar reminders are on for the daemon's profile.
   - A person's `hz system daemon tick`, an MCP `system.daemon.tick` and `hz system serve daemon` never start a lane.
     They return as they do today.
   - At most one lane exists per profile. A lane marker in the daemon state directory, holding the parent PID, the
     child PID, the start time and the window, prevents a second lane.
2. **The lane process.** At its start the tick runs the migration (point 9), then starts
   `hz org calendar-reminder-lane`. That child boots built-ins plus the calendar capability, not plugin jobs. After
   that the tick runs its jobs as today.
   - The lane runs 0126's cycle: refresh at least every 30 seconds, wake at the next due instant, revalidate before
     delivery.
   - The parent tells the lane that its jobs are done by closing a pipe it handed to the child.
   - **Locks.** The parent holds `lifecycle.lock` and `tick.lock` only while it runs its jobs, as today, including
     the handling of abandoned job threads. Once its jobs finish it releases both and closes the pipe. It then waits
     for the lane while holding no daemon lock. `job_running()`, the auto-updater, `pause`, `resume`, `install`,
     `restart` and `uninstall` therefore see the daemon idle while only a lane runs. The lane itself takes only
     0126's per-cycle fence.
   - The tick exits only after its jobs are done and the lane has ended. The manager counts one job, one label and
     one consent.
   - **Crash.** If the child exits before its window (provider bug, out of memory), the parent restarts it with
     backoff: at most three restarts per tick, 5, 15 and 45 seconds apart. Each crash is recorded in the heartbeat.
     After the third one, the lane is overdue (point 13) until the next tick.
   - **Teardown past the slot.** If, after the lane exits, the wall clock is already at or past `s − ε` (one second),
     the parent starts a new lane for the next window instead of exiting, and records an overrun. A slow teardown
     therefore cannot drop the next firing and leave an interval without reminders.
3. **The window rule.** The lane may end only when its tick's jobs have finished and the wall clock is inside the end
   window `[s − m, s − m/2)` of the next local slot `s` (margin `m`, point 6).
   - If the lane is still running jobs at the window, or finds itself past the window, it targets the window of the
     following slot. That happens after sleep, a forward clock step or a slow cycle.
   - The lane checks the wall clock on every cycle and caps every sleep at 30 seconds, so a wake or a clock step is
     noticed within one cycle.
   - If a tick crosses a slot, the manager drops that firing. Jobs catch up from the ledger, the overrun is a health
     finding, and reminders stay covered.
4. **Slots in local time.** The lane computes `s` in the local wall time of the manager (launchd `Minute`, systemd
   `OnCalendar`), once per cycle and after `time.tzset()`. Zones with :30 and :45 offsets and time-zone changes while
   travelling are therefore correct. A backward step ends the lane at the new next slot. Whole-hour DST shifts do not
   move minute-aligned slots.
5. **The boundary gap.** Most reminders are due at :25 and :55, which are slot boundaries.
   - In its last cycle the lane delivers every reminder due before the next lane can be running: up to
     `end + m + H`, where `end` is the window end `s − m/2` and `H` is the measured start-up time of a lane, capped at
     90 seconds. On systemd, `s` includes the fixed delay (point 7).
   - This needs a planner change: in the last cycle only, a reminder counts as due when `due <= now + lookahead`.
   - Such a reminder comes up to `m + H` seconds early (105 at most), never late. The next lane will not repeat it,
     because 0126's replay keys deduplicate delivery. Quiet hours still apply to the actual delivery instant, so a
     07:05 meeting with working hours from 07:00 is a test case.
6. **The margin.**
   - `m` covers the last cycle, teardown and timer leeway. It is fixed from measurements on macOS and Linux, starting
     at 15 seconds.
   - Agenda reads in the lane have a hard timeout of 8 seconds. A read starts only when `end − now` is more than that
     timeout, with `end` the window end `s − m/2`.
   - Because of point 3 a missed margin costs one dropped tick, not a gap, so the margin only has to be usually
     enough.
7. **Timer settings (amends 0020 decision 1).**
   - systemd: the timer gets `AccuracySec=1s` and `FixedRandomDelay=true`, so the per-machine delay stays constant.
     The lane reads `s` from the timer's `NextElapseUSecRealtime` and never recomputes systemd's delay hash.
   - systemd: the service gets `TimeoutStartSec=infinity`. Several jobs run one after another, so no fixed sum fits;
     the lane's own window rule and the job timeouts bound the run.
   - Heinzel starts the oneshot only with `--no-block`, because it stays `activating` for the whole lane.
   - launchd: `StartCalendarInterval` has no jitter and needs no change.
8. **Lifecycle code that assumes a short tick.** Package handover (`idle_package_handover_matches`), install recovery
   (`_recover_install`), `_service_manager_pid` and the update kickstart all read a live manager PID as "a tick is
   running". With reminders on, that is almost always true.
   - These paths treat a lane with no running jobs as interruptible. Because the parent released its locks
     (point 2), they can take the daemon lifecycle lock.
   - The gate is the SDK maintenance gate that 0126's fence checks: the installation mutation marker
     (`installation_mutation_pending`) under the host maintenance lock. The updater sets it. `_recover_install` and
     package-handover recovery set it while holding the daemon lifecycle lock.
   - With the gate set, the updater sends SIGTERM to the lane child named in the lane marker. It sends it only after
     checking that the PID's process birth time matches the marker. The lane exits at once and the parent exits after
     it. The updater does not wait for the next cycle.
   - The daemon counts as idle only when the manager reports no PID after the gate is set, never because of the lane
     marker. Only then does the updater kickstart the daemon once.
   - Reminders therefore come back within seconds after an update, as with 0126's worker, not at the next slot.
   - With jobs running, they wait as today.
9. **Migration from 0126 units.** Reminders now run for one profile per daemon: the profile the daemon is bound to.
   - The retained `calendar-reminder-worker` first checks whether a daemon lane owns its profile. That means a fresh
     lane heartbeat (point 13) carrying this profile's identity, not merely a daemon bound to the profile. A paused
     or broken daemon therefore never makes the worker remove itself with nothing to replace it.
   - If a lane owns the profile, the worker removes itself in this order. It verifies its unit's bytes, then unlinks
     the unit file, and only then boots itself out (`launchctl bootout`; on systemd `disable --now --no-block`), and
     exits. A worker that booted out first would be killed before unlinking, and launchd would load it again at the
     next login. If no lane owns the profile, the worker keeps working as under 0126.
   - `hz system update`, `system.daemon.install` and the tick before its lane also remove a byte-matching unit of the
     daemon's profile. They take the SDK maintenance lock and the calendar profile lock as one `MaintenanceLocks`
     union, which acquires its paths in sorted order. The tick takes it without waiting and skips migration for this
     tick if the union is busy; it already holds the daemon lifecycle lock while it runs jobs. Migration runs before
     the lane starts.
   - In a profile that is not the daemon's profile, Calendar never offers to install the daemon (that would rebind or
     refuse its one per-user label). It says "Reminders run for the profile this computer's Heinzel daemon uses."
   - Units of other profiles keep working until they are removed.
   - Modified, linked, foreign or shim-changed units are never touched. Doctor lists each leftover unit with its exact
     removal action.
   - The worker and its uninstall operation remain while 0126 units can exist. Doctor's leftover finding names a
     release after which the worker only removes its own unit or reports, so the operation can go later. The install
     operation and its separate confirmation are withdrawn.
10. **0133 inventory.** The calendar namespace stays registered for SDK uninstall. It gains a per-unit classifier for
    migration and doctor: owned and matching, owned but changed, or foreign. Uninstall keeps refusing anything it
    cannot classify, as today.
11. **Consent and disclosure.** A lane needs the daemon (0078 point 3) and the Calendar reminders setting, which is
    user-scoped and off by default. Project configuration cannot grant it, and there is no third step. The copy
    changes:
    - Next to the Calendar reminders toggle: "the background helper reads your calendar about every 30 seconds, also
      while the app is closed". When the setting is turned on and no tick is running, the app kickstarts the daemon
      once, so the first lane starts at once rather than at the next slot.
    - Pause: "also pauses calendar reminders".
    - The daemon pane lists Calendar reminders as keeping it running, with the lane's memory.
12. **Setup from Calendar.** Calendar's single action calls the existing `system.daemon.install` or
    `system.daemon.resume` operation through 0078 point 3's trusted confirmation. 0078 already moved install to the
    web head; this ADR adds no operation and no REST or MCP path.
13. **Heartbeat and status.**
    - The lane writes a heartbeat when its state changes, and at least once a minute (never more than every 30
      seconds). It records the PID, version, profile identity, window, the last cycle result (`ok`,
      `provider-unavailable`, `disabled`, `gate`), crashes and restarts, and the last slot overrun.
    - The lane is *overdue* when its heartbeat is older than two minutes and the last slot and the last wake (wall
      time minus monotonic time) are each more than two minutes ago.
    - `org.calendar-reminders-status` reads the daemon and lane state through the `DaemonAttestation` port, which
      gains a lane field. It does not import `system.daemon`.
    - Calendar shows one line, with one action, only when reminders are on and the daemon is missing (install), the
      daemon is paused (resume), the lane is overdue (open the daemon pane), or the provider is unavailable (connect).

### Consequences

- Good: one job, one consent, one status. Reminders run whenever the daemon runs and the setting is on, after first
  run and after every update.
- Good: long jobs, sleep, clock steps and updates leave no gap. Reminders due at slot boundaries come slightly early
  rather than late.
- Bad: with reminders on, the daemon effectively stays present, and its lane memory is shown.
- Bad: correctness relies on the manager dropping a firing while the job is still running, which must be observed,
  not assumed. The lifecycle code gains a lane-aware state.
- Bad: other profiles keep their 0126 units until they are removed.

### Confirmation

- Unit tests with fake clocks, managers and supervisors cover every rule of points 1 to 13.
- In isolated home directories, on launchd and on a systemd user manager:
  - a firing during a running lane is dropped, not queued;
  - the lane survives sleeping for an hour in the middle of a lane and waking three minutes before a meeting, and
    the reminder is delivered;
  - a seven-minute job on a five-minute interval leaves no gap;
  - for a meeting at :00 at the default interval, the delivery offset is measured on a loaded machine;
  - on Linux, a 60-minute interval does not kill the lane through `TimeoutStartSec`;
  - with interval 10 and `TZ=Asia/Kathmandu`, the lane ends at local slots;
  - migration with a second profile's unit, a modified unit and a shim-changed unit causes no respawn loop, and
    doctor names each leftover unit;
  - handover after an update, and install recovery, both work with a lane running.
  - while only a lane runs, `job_running()` is false and an automatic update proceeds;
  - a killed lane child is restarted with backoff, and a teardown that crosses the slot starts a new lane;
  - a worker whose profile has a fresh lane heartbeat unlinks its unit before booting out and does not return at
    the next login, and one whose daemon is paused keeps working;
  - a 07:05 meeting with working hours from 07:00 is not delivered early, before quiet hours end.

## More Information

This ADR replaces these parts of 0126: "separately installs a user service", "no long-lived process is installed
automatically", and the separate setup confirmation. 0126's deadline, consent, expiry, replay and profile-binding
rules stay unchanged.

In 0020 it amends:
- "each firing runs a short-lived process", for manager-bound ticks with a lane;
- the rejection of in-process timers, since the lane is a bounded child process;
- the timer settings of decision 1;
- which ticks run a lane.

0020's statement that `install` and `uninstall` are CLI-only was already superseded by 0078.
