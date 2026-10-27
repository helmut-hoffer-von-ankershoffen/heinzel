# Desktop production gates

The shared lifecycle authority is a source foundation. Electron installation and the complete native lifecycle
are not shipped by this change. The broader native candidate is preserved separately for follow-on review.

## Build-owner trust and payload inputs

A production package needs an independently authenticated public catalog root, expected root and policy digests,
canonical HTTPS metadata/target locations, release channel intent and exact compatible app/core/plugin versions.
A new local self-signed root or hashes merely measured from incoming bytes cannot establish a publisher's identity.
Signing private keys must never be included in an application or bootstrap payload.

The build owner must also supply the authenticated standalone Python payload and complete resolved wheel closure
for each supported platform, plus an independently authenticated uv executable/version/hash descriptor. Service
paths must point to persistent managed deployments, never a disposable uvx cache or an ambient PATH executable.
The native bootstrap producer and its concrete command/configuration interface are deferred to their own PR;
this authority base offers no public install command or working production catalog configuration.

## Required follow-on proof

- Connect catalog verification, closed resolution and sealed bootstrap preparation to the native owner. Preserve
  original channel/ref/pin intent separately from resolved digests; refuse incompatible retained profile data.
- Require separate explicit native consent for preparation, activation, background work, Open at login and OS
  permissions. Preserve or refuse each window's unsent work before restart/removal. Reuse the existing daemon.
- Verify app/core/UI/plugin update coordination, external package-manager ownership, crash recovery, schema-aware
  rollback limits and Chromium profile compatibility. Do not promise an unsafe data downgrade can be undone.
- Build and validate real macOS and Linux packages through clean install, upgrade, rollback and uninstall. Distinguish
  removal of managed runtime bytes from Electron app removal, service unregistration and uv-installed CLI removal.
- Verify native notification consent and actual delivery without duplicate consumers or false delivery claims;
  capture real opt-in system/microphone audio samples with truthful permission states and no automatic recording/upload.
- Complete production signing/notarization and artifact provenance with reviewed publisher access, then verify the
  actual distributed artifacts. Local fixtures, ad-hoc signatures and API availability are not that evidence.

No OS registration, permission prompt, notification recipient or real user profile is needed to review/test the
authority base. Credentials should be requested only when a concrete reviewed build requires them; their absence
does not prevent further source, fixture and packaging development.
