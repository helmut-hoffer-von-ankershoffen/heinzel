---
status: proposed
date: 2026-11-03
implementation: partial
implemented-by: 485
decision-makers: maintainer and independent security reviewer
amends: 0046, 0078, 0116
amends-0046: offer explicit OS supervised web availability after login
amends-0078: retain the supported uv web server as a separate availability mode
amends-0116: sample durable mutation gates before supervised startup and restart
related: 0020, 0047, 0083, 0084, 0087, 0108, 0110
---

# 0117: Persistent web access has an owned OS controller

## Context and Problem Statement

The scheduled daemon runs short-lived ticks; it does not keep the web workspace available.
`hz ui` remains foreground, and [ADR 0046](0046-an-installable-web-app-with-paired-devices.md) defers start at login.
The [managed backend](../../desktop/managed/README.md) retains a listener and recovers owned workers, but requires
immutable inventories and an explicit controller pipe. It is a private verification library, not installed OS
integration for the supported uv application. A detached foreground process provides no recovery after its death
or restart after login.

## Considered Options

- **Explicit OS supervision of the supported uv web server** (chosen).
- A detached process or shell restart loop: does not establish OS ownership, startup or safe uninstall.
- Add web startup to scheduled ticks: couples availability to scheduling and permits competing process owners.
- Migrate existing uv applications to the immutable managed broker immediately: requires separate verified
  migration, provisioning and updater coordination before preserving the current supported installation.

## Decision Outcome

Chosen option: **explicit OS supervision of the supported uv web server**, because it preserves the existing
installation, paired devices and update handoff while making process availability an owned product operation.

1. **Explicit choice.** The registered `persistent_web` feature flag defaults off while real OS lifecycle acceptance
   remains outstanding (ADR 0115). Installation, attachment, explicit restart and supervised startup require the flag;
   inspection and owned removal remain available when it is off. With that preview enabled in user configuration,
   `hz ui install --port 8737` installs a separate per-user web service; `--dry-run` shows its
   exact files without mutation. `hz ui status`, `restart` and `uninstall` manage that integration. These operations
   are CLI only, and persistence mutations are refused by chat policy even after a proposed confirmation. Installing
   the standard app, enabling the scheduled daemon or enabling Tailscale never silently installs web persistence.
   The root `ui` module provides these operations; its CLI callback retains the existing foreground command.
2. **Ownership.** A private installation record binds port, generation, stable uv shim, exact unit bytes and exact
   external launcher bytes. Installation verifies the current owned uv installation and refuses an occupied port,
   edited or foreign integration, unsafe filesystem paths and unsupported user sessions. Mutations hold one private
   lifecycle lock. Before restart or removal, launchd's loaded program, arguments and definition path, or systemd's
   loaded executable, arguments, fragment and absence of drop-ins, must match the recorded installation. A PID or
   matching file on disk alone cannot authorize stopping a process. After an authorized stop request, a five-second polling
   budget permits known owned transitions to settle; the existing bounded manager-command timeouts still apply.
   Every observation rechecks the exact loaded
   identity and unchanged record; unknown or foreign evidence aborts immediately, and timeout retains all files.
   Waiting never issues another stop request.
3. **One OS process owner.** macOS uses a LaunchAgent in the user's GUI domain with RunAtLoad and throttled KeepAlive;
   Linux uses a systemd user service with a fixed restart delay. Both promise availability after that user logs in,
   not before login or after logout. Neither installs a root daemon nor enables Linux linger. The job runs a
   private exact launcher outside the mutable uv environment, then replaces itself with the stable uv bin shim.
   The shim is never resolved into a disposable uvx environment or pinned to a replaced tool interpreter.
   The owned record binds HOME, user and explicit configuration selectors, all four storage roots and uv tool
   destinations. The launcher clears inherited credentials and configuration, retaining only named OS-session
   capabilities such as the user bus and display. Behavior stays in the selected configuration files: installation
   refuses process-only HZ overrides, and later feature or automatic-update changes are reread without an installed
   environment snapshot overriding them. Cleanup validates the recorded profile independently of caller overrides.
   The launcher pins the exact guarded state directory and a controlled PATH including the captured verified uv
   executable directory, so config/environment changes and a minimal user-manager PATH cannot bypass its journal
   or make uv disappear from the supported updater.
4. **Private local control.** A supervised server suppresses the session URL announcement and publishes control
   only after its listener starts. A bounded owner-only AF_UNIX channel returns the actual web lifecycle instance,
   loaded version, port, service generation and PID. Status requires agreement with the loaded OS job and private
   runtime publication; a loaded job or HTTP 200 alone is not ready. The socket uses a short, verified owner-only
   temporary directory derived from the physical profile path, avoiding Unix socket pathname limits. A new random
   socket name on every instance avoids adopting stale crash endpoints. Retiring cleanup removes only its own
   socket inode and matching publication.
5. **Authentication.** The network bearer stays in memory. No unit, launcher, installation record, runtime
   publication, status result or OS log receives it. Explicit local `hz ui` opens the owned existing service through
   the private channel; `--no-browser` prints that requested fragment URL only to the caller's terminal. Paired
   device hashes, expiry and revocation stay in the existing user state, so devices survive cold restart. A cold
   start rotates the owning token. Existing API authentication, Host/Origin/fetch-site checks, CSP and explicit
   HTTPS-origin sharing remain unchanged. Tailscale remains a separate choice.
6. **Updates and restart.** Normal manual and automatic updates retain the supported updater, announcement,
   postpone, idle-work, exact-version, source-pin, trial, rollback and daemon-handover policies. The supervised
   generation is an explicit command argument, preserved by the server's existing same-PID exec with inherited
   listening socket and token. Trial starts use their existing separate command and temporary state; they do not
   inherit supervised mode or publish a live control endpoint. The OS adds no second package updater.
7. **Unresolved installation changes fail closed.** The external launcher samples every existing durable package
   mutation gate before importing mutable tool code: the complete `auto-update/before-update` snapshot, daemon
   `mutation-pending.json` and `standard-install-pending.json`, and the selected tool's `.heinzel-startup-pending`.
   Any entry, including a dangling link, refuses startup without executing the shim or emitting credentials.
   Ordinary verified daemon handover requests do not block startup. Explicit local control and web API restarts
   also refuse while a mutation gate is pending. These are sampled pre-import/restart checks, not atomic
   coordination with a concurrent writer. Repeated OS attempts remain throttled. Refusal does not establish
   automatic recovery: the existing mutation authority or supported repair must settle the interrupted change.
   No new continuity checkpoint or untrialed runtime is silently endorsed.
8. **Removal.** Uninstall first unloads/stops the exact owned job and verifies the stop, then removes matching
   integration and launcher files. Foreign, edited or unknown loaded identities are left untouched and produce an
   error. If all integration entries are absent by lstat, uninstall returns without querying or touching any
   service manager, allowing never-enabled installations to be removed without a Linux user bus.
   Data, paired devices, scheduled work and Tailscale keep their existing settings. The uninstall skill removes
   web persistence before invoking uv removal and propagates its failure rather than orphaning a respawning job.
   Forced external `uv tool uninstall` is not proof of product-service cleanup.

### Consequences

- Persistent browser access reuses the existing supported application and authenticated restart behavior.
- The product maintains a second OS integration, a bounded local control transport and explicit ownership evidence.
- Interrupted updater recovery can deliberately leave the service unavailable. Safe refusal is observed separately
  from recovery, and OS login/reboot recovery requires platform acceptance beyond fixture process tests.
- This compatibility mode does not claim the immutable deployment guarantees of the future native host.

### Confirmation

Isolated manager and real Unix-socket tests cover exact ownership, foreign ports/jobs, unsafe records, concurrent
mutation, token-free status, failed loads/stops and preserved data. An installed-wheel journey replaces the uv tool
in place, verifies same-PID exec with its retained authenticated session, kills and relaunches the server, then
checks owning-token rotation, paired-device access and revocation; the external guard test rejects pending recovery
before any tool code runs. Actual OS crash restart and login/reboot recovery, plus independent written security
review confirming no weakened check, test, threshold, limit or gate, remain release acceptance requirements.
