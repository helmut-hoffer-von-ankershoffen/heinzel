# Plugin changes retain verified standard-app wheel provenance

The published 0.9.15 standard composition installs five hash-verified wheels through transient local overrides.
The installer deliberately removes those overrides from the uv receipt so ordinary updates can follow the registry.
It retains the installed wheels' `direct_url.json` provenance. With uv 0.13.7, adding a new requirement without
`--reinstall` correctly leaves those existing wheels installed. The metadata verifier then compares their direct
sources against the receipt's bare registry requirements and refuses the change. The published example plugin's
`heinzel>=0.9.1,<0.10` dependency includes 0.9.15; its declared compatibility is not the cause of this refusal.

A plugin mutation now captures the exact hash-bound local wheel sources of unchanged requirements, only when the
complete previously verified startup checkpoint matches. It excludes the requested added or replaced distribution.
Those sources become transient constraints for both uv and the existing strict metadata verifier. The original
receipt constraint policy is restored only after the exact candidate generation, source hashes and versions pass
verification. uv retains local constraint paths but drops their hash fragments in its receipt; cleanup therefore
matches the exact captured paths while the preceding metadata verification independently requires the hashes.
The next ordinary update continues to follow the original receipt requirements.

The real-uv regression installs two wheel overrides, normalizes the receipt through the production helper, records
continuity, and adds another package through the production mutation helper. The original implementation fails at
strict metadata verification. The corrected implementation retains both original provenance records, adds the
selected requirement, and removes all transient constraints. Missing checkpoints, generation changes, malformed
or ambiguous wheel metadata, source/hash drift and changed-plugin source inheritance have focused negative tests.
Existing installation recovery and foreign executable/metadata refusal tests remain required.

Local published-composition diagnostics isolate HOME, configuration, package cache, tool store and managed Python.
The initial standard-install service-manager quiescence boundary uses a declared absent-service fixture; no real
service is stopped, loaded or mutated. This does not establish public bootstrap, service-manager handover, hosted
Linux/macOS acceptance, browser installation or a new published release.
