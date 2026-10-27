# Desktop release catalog contract

`catalog.js` validates schema version 1 and selects exactly one artifact for an exact app version, core version,
platform, and architecture. Each record binds the compatibility tuple to a canonical stable HTTPS URL and a lowercase
SHA-256 digest. `signature.js` verifies a detached Ed25519 signature over a canonical JSON envelope using an explicit
caller-supplied map from key IDs to raw 32-byte public keys encoded as unpadded base64url. Both modules are offline and
do not download, install, execute artifacts, or mutate machine state. Run their focused tests from the repository root
with:

```sh
node --test desktop/catalog/test/*.test.js
```

Successful signature verification returns a detached `{key_id, catalog}` result: that outer result, its catalog,
artifact array and every artifact are immutable. Artifact selection returns a separate frozen flat snapshot, so
later changes to a caller-owned catalog cannot alter an already selected URL, platform, versions or digest. The
existing desktop test command and Electron CI job run these regressions alongside the shell tests.

The signature verifier is a cryptographic utility, not a production trust integration. A caller must pin and ship
trusted public-key roots independently of the catalog; passing a key supplied alongside the catalog establishes no
authenticity. No production key is embedded here. Until a separately reviewed bootstrap verifies a pinned trust root,
catalog entries must not be used to install or execute artifacts. SHA-256 detects byte changes relative to the signed
catalog but does not authenticate a publisher by itself.

The signed envelope has exactly `schema_version`, `key_id`, and `catalog`. Its UTF-8 bytes must equal canonical JSON
with object keys sorted lexicographically and no insignificant whitespace. The detached signature is 64-byte unpadded
base64url Ed25519. The supplied trusted-key map must contain the envelope key ID and its corresponding raw public key.

Schema v1 has exactly two top-level fields: `schema_version` (the integer `1`) and a non-empty `artifacts` array.
Each artifact has exactly `platform`, `architecture`, `app_version`, `core_version`, `url`, and `sha256`. Versions use
canonical semantic version syntax without a leading `v` or build metadata. Supported platform values are `darwin`,
`linux`, and `win32`; supported architectures are `arm64` and `x64`. Every platform/architecture/app/core tuple and
every URL must be unique. URLs must be canonical HTTPS URLs without credentials, explicit ports, query strings,
fragments, or a trailing slash.
