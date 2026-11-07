# Explicit recovery of a legacy daemon

Older daemon records did not identify their configuration profile. Ordinary install, update and removal retain
unknown legacy definitions. Do not edit their records or unload an unverified label to bypass that refusal.

For normal removal, run `hz system uninstall`. It stops verified owned services before removing the installed
program, and keeps your data and settings. Running `uv tool uninstall heinzel` removes the program and its command
links without calling Heinzel to remove its services. It can therefore leave service records behind.

If `uvx heinzel@latest` reports that a retained daemon cannot be verified, inspect its status first:

```sh
uvx --from heinzel hz system daemon status --json
```

The recovery console is available in published Heinzel releases from 0.9.16 onward. It can inspect an older daemon
before replacing it. Use the current release for normal recovery; this does not permanently pin your installation:

```sh
uv run --no-project --with heinzel python -I -m heinzel.heads.daemon_recovery plan
```

Planning is read-only. The initial recovery protocol supports recognized macOS 0.9.14 and 0.9.15 definitions only,
with the independently verified installed standard app, safely owned exact files, the fixed user manager and no
running tick or pending lifecycle operation. A genuinely completed 0.9.15 update handover can qualify only when its
request, observation, scheduled heartbeat, signatures and current verified program fingerprint/version agree. The plan
explicitly archives that completed request; unknown or active requests refuse.

Explicit or different configuration routing refuses. Linux legacy recovery is not implemented; ordinary lifecycle
ownership checks remain in effect on both platforms.

Read the entire plan. Historical profile ownership remains **unknown**. If launchd does not expose HOME, a plan
can select only the verified manager UID's safely owned native account home, matching the current default routing,
after complete environment observations exclude foreign HOME and configuration overrides. The plan discloses that
selection; it does not claim which profile an old process used.

To approve removal of only the shown registration and preservation of its exact unit and marker, confirm its
current digest:

```sh
uv run --no-project --with heinzel python -I -m heinzel.heads.daemon_recovery retire --confirm PLAN_SHA256
```

Any drift refuses. Pause, configuration, data and schedules remain. A completed retirement has a separate historical
receipt and preserved originals.

Retirement is not a new installation or a continuity checkpoint. After successful retirement, run the normal
installer again and choose the services you want:

```sh
uvx heinzel@latest
```

Retirement does not install or update those services automatically.
Repeating a completed retirement digest reports that historical result and never retires newly installed units.

An interruption leaves a private transaction and may retain a blocking lifecycle gate. Never remove that gate or
retry a manager effect by hand. Inspect an explicit restoration plan, then confirm its new digest only if it shows
the intended original registration and files:

```sh
uv run --no-project --with heinzel python -I -m heinzel.heads.daemon_recovery restore RETIREMENT_SHA256
uv run --no-project --with heinzel python -I -m heinzel.heads.daemon_recovery restore RETIREMENT_SHA256 --confirm RESTORE_PLAN_SHA256
```

Restoration refuses changed targets, foreign state and completed retirement. An uncertain outcome remains gated;
a later read is not an acknowledgement of an earlier manager request. Installed tests with a manager protocol
double establish package and command behavior, not native manager acceptance or a completed published upgrade.

## An update interrupted by a restart or crash

An update holds `mutation-pending.json` in the daemon state folder while it changes the installed program. If the
computer restarts or the update's process ends before it finishes, that marker stays, and the web service and every
lifecycle command refuse to start. Never remove it by hand.

The next daemon tick, `hz system update`, `hz system daemon restart` or `hz system daemon install` recovers it when
the evidence proves the update ended: no other lifecycle writer holds its lock, the marker comes from an earlier boot
or from a process that no longer exists, no rollback copy or other pending change from that update remains, and the
installed program still matches its verified startup checkpoint. The marker is moved, unchanged, to
`abandoned-mutation-DIGEST.json` beside a `.receipt.json` that records this evidence. No settings, data, sign-ins or
services are changed. The web service starts by itself within seconds afterwards; `hz ui status --json` confirms it.

When any of that evidence is missing, the marker stays and the commands keep refusing. That includes a web server
that ran the update and is still running: its marker is recovered once that process has ended, for example after a
restart of the computer. An update that stopped while it still held its rollback copy (`auto-update/before-update`),
or after it asked the daemon to take over, is not covered by this recovery. Check
`hz system daemon status --json` and `hz ui status --json`, and the web server's own errors in `web.err.log` in the
log folder (`hz system config get hz.log_dir`).

## Interrupted macOS update from 0.9.21 to 0.9.22

This recovery applies when the automatic updater installed and tested 0.9.22, but could not finish handing off the
scheduled daemon. The old web server can still be serving 0.9.21. The published files cannot by themselves prove
that a failed automatic update and a pending mutation belong to the same attempt. A manual update could leave
a mutation that a later automatic attempt refused.

Use this command only when independent evidence from the incident establishes that the automatic attempt reached
the handover after its trial. Use the mutation, handover request and automatic update failure SHA-256 digests
captured for that incident, and a reviewed release that offers this command:

```sh
uv run --no-project --with heinzel python -I -m heinzel.heads.daemon_recovery package-handover \
  --incident-mutation MUTATION_SHA256 --incident-request REQUEST_SHA256 --incident-failure FAILURE_SHA256
```

The plan requires the existing installation checkpoint, an owned and idle launchd registration, and matching
same-boot request, pending mutation and automatic update failure records. It refuses when a rollback copy or
another mutation is present, the selected installation or profile is uncertain, or a new tick already ran. It
does not rerun the package update, replace a checkpoint or start a service.

Read the plan. If the independent incident evidence is missing or inconclusive, leave the gate in place. To
confirm a supported recovery, pass the same incident digests and the current plan digest:

```sh
uv run --no-project --with heinzel python -I -m heinzel.heads.daemon_recovery package-handover \
  --incident-mutation MUTATION_SHA256 --incident-request REQUEST_SHA256 --incident-failure FAILURE_SHA256 \
  --confirm PLAN_SHA256
```

Confirmation moves the exact mutation marker to the archive shown in the plan. The original handover request
stays in place. Wait for the existing schedule to start the daemon. Check `hz system daemon status --json` for
an owned, loaded daemon with a fresh 0.9.22 tick and no version mismatch. Only then use `hz ui restart` if the
owned web service is still serving 0.9.21, and check `hz ui status --json` for 0.9.22.

If confirmation was interrupted, check the archive and status. An existing archive records the move. Only the
daemon's own fresh tick proves the handover. Leave other pending markers in place.
