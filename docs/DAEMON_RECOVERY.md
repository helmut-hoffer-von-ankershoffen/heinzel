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
