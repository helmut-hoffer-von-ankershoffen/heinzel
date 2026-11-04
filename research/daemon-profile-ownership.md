# Daemon replacement binds the selected profile and actual manager identity

A daemon is a fixed per-user manager label. The old installer wrote only its executable/arguments and PATH. It did
not preserve the selected configuration file or data/state routing, and replaced the fixed label before proving
that the loaded job belonged to this profile. A successful manager command alone could therefore replace another
profile's service or report success for the wrong loaded identity.

The unit now carries explicit HOME, configuration-home, optional explicit configuration file, and effective
data/cache/state/log/unit paths. Its private install record binds that routing with a digest. Credentials and
arbitrary environment values are not copied. Relative, parent-traversing and control-character routing refuses.
Replacement requires the verified uv target, exact safely owned generated files, matching profile record, and
fixed bounded manager authority in the verified user session. The loaded path, program, complete argv, environment
and working directory must agree. Linux also refuses drop-ins, queued jobs and unknown control/PID state.

Lifecycle and tick locks serialize changes. An existing stopped service stays stopped and a pause stays paused.
A failed load reports restoration only when the prior files and actual activation are verified restored;
otherwise the durable pending gate remains and no successful rollback is claimed. Normal daemon uninstall,
handover and tick PID attestation consume the same ownership policy. `status.owned` is separate from installation,
activation and observed tick version; missing or uncertain ownership never makes onboarding complete.

No automatic legacy retirement remains. Released unit bytes, a missing executable or a dead PID cannot prove the
selected profile or authenticate a loaded job sharing the label. Unknown definitions are not booted out, moved or
deleted. Exact current-format stopped definitions can be retained without mutation after manager absence is proven.

## Observed local checks

The focused unit/integration run passed 302 cases, including foreign label/program/argv, changed files and profile,
manager UID, queued execution, inactive/paused preservation, verified restoration and uncertain recovery. Manager
fixtures are explicitly protocol doubles; they never call the real launchd or systemd.

An immutable installed candidate SDK wheel matched all 324 Python source files. With uv 0.13.7 and private HOME,
configuration, tool store/cache and managed Python, real CLI installation, owned status, pause, restart and removal
passed for fresh default and explicit alternate profiles under the declared manager double. Normal paused tick
argv ran twice with the captured routing and a guarded tool PATH. This establishes installed profile behavior,
not real OS scheduling or the full unmodified manager environment.

The actual published 0.9.15 SDK accepted and replaced a foreign loaded-program double. The candidate refused the
same case before any unit/record changes or manager mutation.

## The safety-only checkpoint left an explicit legacy migration gap

The published 0.9.15 SDK created a genuine default-HOME daemon unit and install record through its real CLI, with
only its manager boundary doubled. After ordinary uv replacement with the candidate, `system daemon install`
refused that legacy record unchanged. Selecting an alternate explicit configuration also refused unchanged.
Those records contain no profile binding, so canonical legacy bytes alone do not establish which configuration
was originally selected. At the safety-only checkpoint there was no supported operation that safely adopted or removed this
unknown legacy integration. Do not present this repair as a completed automatic upgrade for existing users or
suggest bypassing the refusal by editing its record. The orchestrator must resolve that recovery gap or explicitly
bound release acceptance before claiming existing installations can migrate.

The candidate retains the source branch's package version; no version was edited for the proof. This is a
published-0.9.15-input/code-delta check for the next release, not a literal accepted 0.9.16 package upgrade.
A real isolated manager restart was unavailable: the fixed local Docker socket was absent, and no live user
manager, owner profile or installed service was touched. Fresh cross-OS, composed-app and published-release
acceptance remain separate requirements.


## Explicit candidate recovery is separate from ordinary lifecycle operations

[ADR 0132](../docs/adr/0132-explicit-legacy-daemon-retirement.md) adds a dedicated, digest-confirmed module console.
It retains unknown historical profile ownership, verifies the current default scope and exact registration, and
synchronizes private backups before any unload or removal. Verified completed 0.9.15 handover requests are archived
as an explicitly disclosed separate effect; observation and heartbeat remain data with preserved copies.

The [installed regression](../tests/e2e/daemon_legacy_recovery_test.py) uses published 0.9.14, its genuine automatic
0.9.15 update and real child tick, explicit retirement, the installed 0.9.15 normal updater and ordinary installation
of a versioned copy of candidate source. Its isolated composition adds a declared manager fixture, and its native
manager boundary is a protocol double. This is not the complete public standard composition, native launchd
acceptance, or an accepted published 0.9.16 release. Linux legacy recovery and unknown evidence still refuse.

The [recovery guide](../docs/DAEMON_RECOVERY.md) explains explicit planning, confirmation and journal-bound restore.
Ordinary installation, update and removal never invoke retirement automatically.
