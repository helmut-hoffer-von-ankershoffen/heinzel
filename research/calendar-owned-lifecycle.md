# Calendar workers and offline removal

This composition preserves the reviewed calendar reminder implementation and the reviewed offline remover through
normal history-preserving merges. Calendar profile workers previously outlived removal of their shared SDK because
the remover inventoried only daemon and web integration. ADR 0133 makes the owning calendar module contribute exact
host-maintenance descriptors through registered built-in provenance; system never imports org or resolves a calendar,
notification, credential or account provider. Disabled feature owners remain available to explicit maintenance only.
Ordinary boot and disabled-feature behavior stay unchanged.

Inventory bounds the physical supervisor directory, parses every recorded profile and requires the complete expected
unit bytes, exact stable tool shim/argv, profile environment and current loaded-manager identity. Foreign files,
changed routing, orphan loaded labels and ambiguous inventories refuse before effects. One SDK-wide lock fences new
profile setup and each worker cycle. The coordinator recaptures inventory under the deterministic union of lifecycle
locks, publishes one durable SDK-wide gate, stops all owned jobs and observes quiescence before native uv removal.
Definitions remain until the SDK is observed gone. Consent, configuration, user data and notification replay state stay.

The copied standalone journal binds full plain-data descriptors, unit bytes/inodes, manager names/facts and physical
lock identities, plus physical definition directories and complete manager namespaces even for an empty inventory.
Before effects it rescans those bounded scopes and refuses extra or changed definitions and newly loaded workers.
It retains this completeness check after the SDK is absent, never imports a provider and never executes a
journal-supplied callback. The in-process path also revalidates its exact gate after the durable removing phase and
immediately before invoking native uv. The original final gate check, changed-gate pre-effect refusal, native uv pins, exact runner
environment, UID/session/Linux bus checks and existing file/output/time budgets remain.

Focused isolated tests exercise both supervisor formats, two active profiles, stop-before-cleanup, foreign profile/unit
and orphan labels, concurrent setup/worker admission, disabled owners with poisoned providers, replaced recovery locks,
crash with loaded workers and copied recovery after SDK removal. Existing reminder scheduling, refresh, expiry, quiet
hours, consent, binding and deduplication assertions are retained. Installed macOS fixtures build real immutable wheels,
run the actual published 0.9.15 updater, and execute native offline removal/copied recovery under OS IP-network denial
with poisoned PATH uv. Their service-manager observations are explicit immutable doubles: no launchd job is installed
or signalled. Calendar setup uses the installed operation for two profiles; retained configuration and genuine notify
store/replay files are checked byte-for-byte. Ordinary native tool listing must still ignore the private authority.

These tests do not establish actual isolated launchd/systemd, Linux native execution, a connected calendar provider,
OS notification delivery or live acceptance. No live user service was changed. Final independent source review and
combined publication/CI/release acceptance are still required.


Independent review found that using the expanded mutation-path generator to authenticate older launchers changed the
historical byte grammar. Six isolated OS/format cases reproduced the refusal using immutable published 0.9.15 wheel
and actual main 505 renderer fixtures. The repair freezes historical bytes separately and keeps historical ownership
out of current readiness and idempotent-install behavior.

Explicit migration and retirement use a separate durable old/new transition journal and deterministic SDK/web locks.
The fixed manager and verified current SDK uv are selected independently of PATH; the historical uv path is never
executed. Both the global SDK gate and an old-shell-compatible profile gate remain through stop/replacement/removal,
with exact authority checks at effect boundaries. Copied offline recovery binds both fences and distinguishes complete
target cleanup from terminal gate cleanup. Real OS-manager, Linux-native and connected-provider acceptance remain
separate from the isolated fixtures.
