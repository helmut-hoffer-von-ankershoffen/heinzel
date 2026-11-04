---
status: proposed
date: 2026-11-04
implementation: partial
decision-makers: maintainer
related: 0006, 0020, 0108, 0110, 0117
---

# 0131: Offline uninstall keeps private installer and recovery authority

## Context and Problem Statement

A standard Heinzel installation must be removable without networking, plugin discovery or a running web UI.
PATH-selected tools, inherited service-manager buses and a successful stop command alone do not establish authority
to remove an installation. Its running SDK may disappear before integration cleanup finishes.

## Considered Options

- **Ship an exact uv runtime dependency and stage authenticated removal bytes locally** (chosen).
- Installer-only artifact caches cannot cover the already published 0.9.15 updater, which cannot provision new caches.
- Resolve uv or fetch an installer while uninstalling: fails the offline contract.
- Execute a pathname merely observed by stat: a group-writable ancestor can replace it before execution.
- Delete integration before invoking uv: loses recoverable ownership after a refused or partial removal.
- Copy or invent continuity for an unmarked SDK: turns unverified state into removal authority.

## Decision Outcome

Chosen option: **pin uv as a Core runtime dependency**, because the immutable 0.9.15 updater already resolves
new Core dependencies. It can therefore install offline removal authority without changing its published bytes.
The dependency is `uv==0.13.7`; supported macOS ARM64/x86-64 and glibc Linux ARM64/x86-64 executables have independently
pinned publisher SHA256 hashes. Other artifact/platform combinations refuse before service effects.
Uninstall selects only the concrete installed distribution under its verified tool prefix, validates bounded opened
metadata, RECORD membership and native bytes, and copies the executable into a physical private recovery folder. Its bytes and inode are rechecked; an offline version probe must succeed before any service stops.
External uv paths, including Homebrew paths, are not executed by removal. Group-writable ancestors remain refused.
Publisher-pinned license material is copied alongside the private recovery artifacts. No uv Python module is imported,
and no PATH or cache fallback is accepted.

`hz system uninstall` and its help dispatch before kernel boot, plugin discovery and background update checks.
Only an existing continuity-verified standard uv tool qualifies. The exact environment runner does not merge ambient
UV policy. Both target directories and all offline/no-download/no-config flags are explicit. SDK, desktop runtime,
unmarked, changed and redirected targets refuse with local guidance.

Daemon and web cleanup use one fixed, session-verified manager authority with a sanitized bus environment. File and
loaded-manager identity are checked before mutation. Preview flags do not hide cleanup of an already owned service.
Lifecycle and tick locks fence cooperating writers. Stop and final file deletion are separate: definitions remain
through the observed uv outcome. Successful removal must observe the tool environment and receipt-owned scripts gone.

Before stopping services, a private journal and a standalone standard-library recovery script are saved and their
directories synchronized. The immutable recovery authority is hash-bound into that copied script; only the observed
phase is mutable. A durable existing installation gate prevents cooperating starts while outcome is uncertain.
Recovery still works through the captured external Python after the SDK is removed. It never starts or signals jobs:
fixed user managers must confirm quiescence before retry. A partial SDK must be a subset of the captured opened-file
manifest; replacement, changed or new entries refuse. After verified SDK removal, recovery also finishes only unchanged original receipt-owned script links.
Cleanup unlinks only unchanged captured integration files,
synchronizes their directories and completes any user-manager reload before clearing its exact gate.

User data, configuration, agent skills, notifier and recovery history stay. There is no purge flag
or automatic network rollback. An uncertain phase is reported as incomplete with its concrete local recovery command.

### Consequences

Offline removal needs no third-party tool from PATH, and its owned cleanup can resume after the SDK is gone.
The runtime dependency adds approximately 28–40 MB of native executable bytes per supported platform. Uncertain authority refuses locally;
actual isolated service-manager acceptance remains a separate obligation.

### Confirmation

Focused tests cover exact runner environments, private executable replacement and writable ancestors, foreign labels,
wrong sessions/buses, missing managers, bounded stop failure, stop-before-cleanup ordering, partial uv refusal,
authority tampering, interrupted phases, unlink/reload/fsync failures and recovery retry without another uv mutation.

Installed macOS fixtures use the real standard installer and immutable built wheels, including an explicit manager-double
module; they do not manufacture continuity or relax startup metadata validation. The installed SDK's real CLI dispatch
and pinned uv run under OS IP-network denial. Separate proof checks that local Unix IPC remains possible. This is not
real OS manager acceptance. Linux fixtures use inherited seccomp IP-socket denial; hosted execution and actual isolated
macOS/Linux user-manager acceptance remain required before claiming those outcomes. The live user manager is excluded.

Publisher provenance: [Astral uv 0.13.7 artifacts](https://pypi.org/pypi/uv/0.13.7/json).
