# Explicit recovery of a legacy daemon

Older daemon records did not identify their configuration profile. Ordinary install, update and removal retain
unknown legacy definitions. Do not edit their records or unload an unverified label to bypass that refusal.

A reviewed candidate containing ADR 0132 provides a separate module console before the persistent SDK is updated.
Use an exact verified candidate requirement or wheel path in place of `<candidate>`:

```sh
uv run --no-project --with '<candidate>' python -I -m heinzel.heads.daemon_recovery plan
```

Planning is read-only. The initial recovery protocol supports recognized macOS 0.9.14 and 0.9.15 definitions only,
with the independently verified persistent standard app, safely owned exact files, the fixed user manager and no
running tick or pending lifecycle operation. A genuinely completed 0.9.15 update handover can qualify only when its
request, observation, scheduled heartbeat, signatures and current verified SDK fingerprint/version agree. The plan
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
uv run --no-project --with '<candidate>' python -I -m heinzel.heads.daemon_recovery retire --confirm PLAN_SHA256
```

Any drift refuses. Pause, configuration, data and schedules remain. A completed retirement has a separate historical
receipt and preserved originals.

It is not a new installation or a continuity checkpoint. Then use the installed
SDK's supported update command and the new SDK's ordinary daemon installer; retirement does neither automatically.
Repeating a completed retirement digest reports that historical result and never retires newly installed units.

An interruption leaves a private transaction and may retain a blocking lifecycle gate. Never remove that gate or
retry a manager effect by hand. Inspect an explicit restoration plan, then confirm its new digest only if it shows
the intended original registration and files:

```sh
uv run --no-project --with '<candidate>' python -I -m heinzel.heads.daemon_recovery restore RETIREMENT_SHA256
uv run --no-project --with '<candidate>' python -I -m heinzel.heads.daemon_recovery restore RETIREMENT_SHA256 --confirm RESTORE_PLAN_SHA256
```

Restoration refuses changed targets, foreign state and completed retirement. An uncertain outcome remains gated;
a later read is not an acknowledgement of an earlier manager request. Installed tests with a manager protocol
double establish package and command behavior, not native manager acceptance or a completed published upgrade.
