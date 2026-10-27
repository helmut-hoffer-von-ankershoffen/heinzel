# Shared managed-runtime authority

The shared lifecycle authority records and verifies one desktop profile's managed runtime transitions. This base
provides private Python primitives and a bounded local pipe adapter. It does not launch or install an Electron app,
change the ordinary CLI entry point, download packages, register services or request OS permissions.
[ADR 0078](adr/0078-native-desktop-uses-the-shared-lifecycle-and-daemon.md) describes the wider desktop design;
the native caller, authenticated catalog/bootstrap, app packaging and background setup are follow-on work.

## Authority and admission

Only the Python `LifecycleStore` writes activation and transition records. Runtime manifests inventory exact code
bytes and the persistent interpreter; an environment manifest, when present, inventories the entire environment.
Local hashes prove that those recorded bytes are unchanged. They do not establish a publisher's authenticity.
The store refuses unsafe ownership, permissions, links, missing/extra inventory entries and unexpected records.

The closed private request model accepts only the fields required by its selected action. It accepts no executable,
source URL, renderer approval or `human=true` flag. A caller must present the exact expected activation when preparing
a change. `first_install` additionally requires absent activation and absent generation history inside the lock;
it is an admission constraint, not evidence of human confirmation or authority to downgrade retained data.

A transition records durable intent before switching its activation pointer. Only an exact deployment/generation/
version/UI-hash/instance proof completes a switched transition. The eventual native owner must obtain that proof
from its authenticated running worker. Merely writing a pointer or receiving a preparation acknowledgement is
insufficient. Cancellation applies only to the exact unchanged prepared plan; it cannot reverse a switched plan
or cancel a replacement intent. Interrupted switches remain verification-required for the eventual owner.

## Writer lifetime and lock order

The lock order is lifecycle authority, writer barrier, then backend ownership. A command claim verifies its selected
generation and retains a shared OS writer descriptor for its whole execution. Inherited descriptors keep that
lease alive until every owning process closes them. Native activation/uninstall requires an exclusive barrier and
refuses while commands still run. The transition adapter does not claim an ordinary command lease, which avoids
waiting on its own writer. Pre-kernel CLI forwarding and the actual broker are intentionally outside this base.

The private pipe adapter bounds its input and returns structured success or a fixed refusal, without reflecting
rejected paths, tokens or child diagnostics. It is a same-owner local transport, not a public API or permission
issuer. No import starts a process, discovers plugins or creates a desktop profile.

## Removal and recovery

Runtime removal requires the expected activation, exact prepared uninstall plan and an owned backend drain receipt.
An uncertain drain retains the plan and runtime bytes. Deactivation retains the generation high-water mark, so
stale commands cannot regain authority and first-install admission cannot silently reuse historical profile data.

Optional payload cleanup writes its exact checked inventory to a durable cleanup journal. It removes only inactive
managed deployment/environment payloads whose bytes remain known. A changed, linked or foreign file is refused;
interrupted cleanup can resume from its journal. Profile settings, credentials and saved work remain outside those
trees. This is not Electron app removal, service unregistration or `uv tool uninstall`.

## Verification and remaining delivery

Focused unit/integration tests use private temporary inventories and real process/file locks. They cover stale
plans, exact first-install admission, retained history, writer inheritance, refusal before deactivation, bounded
pipe input, interrupted cleanup and retained user data. Fixtures are not signed releases or native OS proof.

Before a production desktop release, subsequent changes must connect the native owner and per-window unsent-work
safety, authenticate catalog/bootstrap inputs, preserve requested channel/ref/pin intent, enforce compatibility and
schema-aware rollback, and verify complete install/update/uninstall on macOS and Linux. Signing/notarization,
explicit existing-daemon/background consent, notification delivery and actual opt-in audio samples remain separate
gates. [Production gates](DESKTOP_PRODUCTION_GATES.md) records the exact trust inputs and missing proofs. This base
does not claim those workflows are shipped.
