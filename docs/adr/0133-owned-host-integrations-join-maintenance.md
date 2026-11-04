---
status: proposed
date: 2026-11-04
implementation: partial
related: 0126, 0131
---

# 0133: Owned host integrations join one maintenance inventory

## Context and Problem Statement

A persistent calendar worker belongs to its profile but executes the shared installed SDK. Removing that SDK must
stop every owned worker of every recorded profile before native removal, while retaining all user data and reminder
consent/replay history. Omitting reminders or refusing every installed worker is not completed uninstall support.

## Considered Options

- Keep fixed daemon/web cleanup only: it leaves calendar workers pointing at a removed SDK.
- Refuse all installations with reminders: it does not provide the required uninstall lifecycle.
- Use complete typed built-in ownership inventories with standalone recovery scopes (chosen).

## Decision Outcome

Built-in owners register typed host-maintenance integration contributions. The host resolves only exact registered
built-in classes, namespaces, Core distribution origin and constructed instances; it never resolves calendar,
credential, notification or account providers during inventory. The owning module supplies bounded exact definition,
profile environment, program/argv, physical file identities, manager labels and lifecycle locks. A generic fixed
user-manager authority retains the existing UID/session/Linux bus checks. Foreign, unrecorded, changed or ambiguous
units refuse before effects; no prefix deletion or persisted provider callback is authorized.

One SDK-wide lock fences profile setup and worker admission. The complete integration inventory is recaptured under a
deterministically ordered union of lifecycle locks. One durable SDK-wide gate precedes every stop and is observed by
calendar setup/worker cycles and the existing daemon/web admission paths. All owned jobs must be observed stopped
before uv. Definitions survive until SDK removal is observed; only unchanged captured files may then be deleted.

The standalone removal journal hash-binds the full plain-data inventory, discovery namespaces and lock union, including
empty namespaces. Recovery bounded-rescans the physical definition directories and fixed manager namespaces before
effects, refusing extra, changed or unrecorded workers without importing owner code. It requires quiescence and
rechecks the exact gate after durable phase writes immediately before native removal, as well as at final cleanup.

Exact published 0.9.15 and main 505 web launchers remain an owned historical category, distinct from current operational
ownership. Their frozen four-path grammar and complete marker/profile/unit tuple authorize inspection and explicit
migration or retirement; edited scripts refuse. The historical uv spelling is inert input to that byte comparison.
Current operations select and verify the installed pinned uv artifact and the fixed user-session manager authority.

Historical shells consume a profile compatibility fence in addition to the SDK-wide gate. Offline recovery hash-binds
both fences and their physical authority, requires both before effects and retains them through target cleanup. A
prepared partial publication can fill only its exact missing fence after full revalidation under all recorded locks;
foreign gates and missing fences after stopping refuse. A durable cleaned phase separates target effects from final
own-gate cleanup, so a crash while clearing gates never authorizes another native removal.

Explicit web install or restart migrates historical launchers before loading or restarting. A separate operation-kind
journal binds old/new bytes, private staged inodes, profile, SDK, manager and deterministic SDK/web lock authority.
After verified stop, it conditionally replaces launcher and marker with the coherent canonical tuple, verifies loading,
then clears only its own fences. Interrupted migration or retirement resumes through its original explicit command;
it never restores or relaunches unfenced historical bytes. The new journal has a separate 256 KiB budget; existing
16 KiB control-file and all service/SDK-removal limits remain unchanged.

### Consequences

Recovery remains usable after the SDK is absent and never invokes arbitrary journal callbacks. Real OS-manager and provider acceptance remain distinct from
isolated manager-double tests. Existing reminder timing, refresh, consent, expiry, restoration and deduplication stay.


### Confirmation

Isolated tests exercise complete multi-profile removal, namespace additions after journaling, foreign files, replaced
locks and changed gates with zero native-removal or cleanup effects on refusal. Installed network-denied fixtures
exercise the actual native remover and copied recovery; real OS-manager and provider acceptance remain unverified.
