---
status: proposed
date: 2026-10-25
decision-makers: maintainer, architect and security reviewer
amends: 0006, 0020, 0043, 0046, 0047
related: 0003, 0027, 0028, 0031, 0032, 0039, 0068
---

# 0078: Native desktop uses the shared lifecycle service and existing daemon

## Context and Problem Statement

The maintainer requests an Electron application for macOS and Linux while retaining the CLI and browser.
Persistent background work, native notification/audio permissions and coordinated app/core/plugin updates need
one owner. The present uv updater changes a live tool environment; notification streams and native fallback do
not have a shared durable attempt protocol. A working local shell prototype does not provide those contracts.

## Decision Drivers

- One Python domain model and scheduling authority across every authorized head.
- A genuine PyPI distribution, persistent uv installation and explicit native installation.
- Stable app identity, profile/origin/storage, private authentication and honest permission/delivery states.
- Candidate validation, recoverable deployment switching and explicit data-migration rollback limits.

## Considered Options

- **A shared lifecycle service, minimal broker and narrow Electron head over the existing web UI** (chosen).
- Reimplement scheduling, notices and updates in Electron: duplicates policy and permits conflicting owners.
- Attach whichever hz is on PATH or listens on a port: cannot establish ownership, compatibility or safe recovery.
- Reinstall the active environment in place: cannot verify a full candidate before replacing running dependencies.
- A custom desktop URL scheme: deferred because service-worker/API/WebSocket parity needs separate proof.

## Decision Outcome

Chosen option: **compose Electron with one shared lifecycle service and the existing daemon**, because native
features need an availability boundary without duplicating business logic or scheduling.

1. A generic lifecycle module owns profile/deployment inventory, locks, plans, update journal, health and uninstall.
   A minimal broker starts without third-party plugin imports, authenticates local control and retains the exclusive
   loopback listener across backend restart/crash/update. A PID or occupied port is not identity. One controller
   owns an owner/profile/channel; additional windows attach. Explicit profiles/channels isolate state and origins.
2. The native head reuses the authenticated UI/API and current CSP. Renderer Node integration remains disabled,
   sandboxing and context isolation enabled. A versioned preload exposes only checked native operations, with
   real main-frame/origin/profile/navigation checks. It exposes no generic IPC, filesystem, process, CDP or proof
   issuer. Bearer identity and renderer-supplied gesture flags are not human approval.
3. Initial setup asks **Keep my Heinzelmännchen working while the app is closed.** It never assumes consent.
   Enabling configures/verifies the existing OS daemon integration through a trusted interaction. One footer
   control shows actual installed/loaded/paused/degraded state, latest/next wake and direct repair/actions.
   Close window, Quit app, Pause all and Open at login are separate. No second timer or silent login installation.
4. Notification policy stays in system.notify. Native main has one subscription; windows and browsers compete
   through the same atomic claim/attempt/fencing protocol. Availability, accepted, OS-reported shown, rejected and
   unknown are distinct. A crash between OS post and receipt is uncertain, not permission to blindly resend.
   One attempt owns sound under the independent notification preference, preventing duplicate dings.
5. System and microphone capture are distinct explicit choices. Only actual local samples reach the analyser;
   no recording/upload or synthetic bars. Track creation is not capture success. Stable signed app identity,
   accurate usage descriptions and actual allow/deny/revoke/ended/no-sample proof are required on each platform.
   Pending picker completions and all tracks stop on cancellation, identity change, quit or update.
6. The distribution is heinzel and the command hz. Persistent uv tool installation is the CLI/bootstrap front
   door; uvx is a disposable foreground trial, never a background-service path. A small pre-kernel dispatcher
   forwards managed commands to the active immutable deployment, with fenced command leases during transition.
   Verified signed Electron artifacts remain separately versioned platform assets rather than a universal wheel.
7. One update coordinator resolves compatible app/core/UI/plugin versions and preserves requested channels
   separately from resolved hashes. Deliberate pins remain pinned; incompatible plugins are reported instead of
   silently removed. Stage and verify before draining writers, switching, authenticating the exact running tuple
   and reloading renderer realms. Executed trusted plugin code cannot be unloaded by disposal.
8. The fsynced journal makes app-bundle and runtime-pointer changes recoverable; multiple filesystem effects are
   not a single atomic operation. Automatic rollback stops at incompatible data/Chromium-profile migrations.
   Linux package-manager ownership disables self-replacement of owned components; Electron's built-in updater
   has no Linux support and does not run as a parallel updater on macOS.
9. Install/login/background/purge choices are explicit typed plans. Uninstall removes only matching owned
   integration and preserves user data by default. External uv uninstall does not prove native services were
   removed. Genuine PyPI publication uses the exact reviewed artifacts and trusted-publisher workflow/environment;
   a missing project or pending publisher does not reserve a name.
10. Native build tooling pins Electron, `@electron/packager` and `@electron/asar`. Packager creates the renamed
    platform bundle; asar independently verifies the bundled runtime inventory and byte hashes. These are development
    dependencies only. Verification builds use the core version without modifying release-owned version files,
    package only compiled native source, preserve one application identifier and carry accurate opt-in audio usage
    descriptions. A successful local build is explicitly unsigned/unnotarized verification evidence, not a release.
11. Candidate staging accepts a closed, hash-checked local wheel inventory from the trusted resolver boundary.
    It disables indexes, builds and startup hooks; installs copied files into a new persistent private environment;
    probes actual core/package/UI versions and runs a hard dependency check. Explicit version constraints must
    agree with the resolved wheels. Requested source intent remains
    separate from the resolved wheels. Both Python and native reads verify the complete environment inventory.
    TypeScript delegates every transition to the one locked Python store instead of writing another journal.
    A copied interpreter still depends on its persistent Python base; a self-contained interpreter/catalog and
    externally owned package installation policy remain separate unfinished distribution work.

### Consequences

- Native and browser/CLI heads share operations, notice policy, durable agents and one scheduler.
- Installation and transitions gain protocol, ownership, migration and target-platform maintenance obligations.
- A local prototype remains useful evidence, while production signing, actual permission outcomes, Linux capture,
  recoverable updates and native installation through the published CLI require their own proofs.

### Confirmation

This first foundation implements the shared transition authority, profile admission, process-held writer leases
and owned runtime removal. Unit/integration tests cover exact plans, running proof requirements, real locks,
stale writers and interrupted cleanup with retained user data. No native application, public catalog/bootstrap,
ordinary CLI forwarding or OS integration is delivered by this slice.
Follow-on native work must add actual window, authenticated backend, package relocation and platform CI proofs.
The eventual native app attaches the shared authenticated web UI only after verifying its active deployment;
a fresh or uncertain profile retains recovery. Catalog and staged wheel provenance need separate verification.
The foreground development shell now verifies its owned CLI's lifecycle before loading that CLI's actual web UI,
with the same fragment adoption and tab storage as the browser head. This is an explicitly narrower admission
than a verified immutable deployment tuple. Exact owned-origin main-frame checks protect the fixed lifecycle
preload, and top-level navigation/redirects cannot carry the session to another origin. Startup cancellation is
checked after both authentication and UI loading, with hidden failed windows destroyed. Native permissions remain
denied until a separate explicit-consent integration. Node tests exercise delayed shutdown; real Electron tests
exercise page navigation and initial/subsequent redirects, while the isolated CLI smoke checks the actual Today
page, authenticated API access, reload, clean owned-child exit and closed loopback listener. This does not establish
signed installation, permission delivery, background operation, recoverable update or complete uninstall.
Before release, add failure-injection tests around every deployment journal/
swap boundary; concurrent singleton/profile and stale-writer tests; actual native notification/capture tests;
browser/native/window claim races; exact running-version checks; owned uninstall and external-owner refusal.
The walkthrough must enable background work, quit Electron, observe a real deterministic agent scheduler wake,
reopen and show its receipt/next wake. Verify a clean persistent uv installation from the genuine PyPI release.
Existing import contracts, authentication/CSP, confidentiality, platform CI and protected-release gates remain.

## More Information

- [uv tool environments](https://docs.astral.sh/uv/concepts/tools/)
- [Electron security](https://github.com/electron/electron/blob/main/docs/tutorial/security.md)
- [Electron audio capture](https://github.com/electron/electron/blob/main/docs/api/desktop-capturer.md)
- [Electron update platform limits](https://github.com/electron/electron/blob/main/docs/api/auto-updater.md)
- [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/)
