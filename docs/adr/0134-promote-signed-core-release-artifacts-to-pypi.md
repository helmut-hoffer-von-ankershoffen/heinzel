---
status: proposed
date: 2026-11-05
implementation: partial
amends: 0079
amends-0079: promote original signed distributions instead of rebuilding
related: 0008, 0114
---

# 0134: Promote signed core release artifacts to PyPI

## Context and Problem Statement

The final GitHub release already builds the macOS notifier, browser app, wheel and source distribution. The PyPI
workflow repeats the notifier and distribution builds serially, adding another macOS runner admission and producing
different bytes for the same version. A registry upload must preserve the original reviewed source and all existing
confidentiality, license, package identity, payload and publishing-authority checks.

## Considered Options

- **Promote the exact signed GitHub distributions** (chosen).
- **Keep independent PyPI rebuilds**: duplicates work and does not establish byte identity across registries.

## Decision Outcome

Chosen option: **promote the exact signed GitHub distributions**, because the same verified bytes can cross registries
without giving build tooling PyPI publishing authority.

The official final-release producer validates its exact tag, main ancestry, release title, project identity and
complete core payloads. It signs the distribution digests and a manifest binding the source commit, caller workflow,
source ref, run ID and attempt. PyPI preparation requires a genuine published final release and a completed successful
official producer on protected main. GitHub cryptographic attestation verification pins the repository, reusable
producer workflow, signer/source digest, source ref and hosted runners; verified SLSA statements also bind the exact
invocation and each subject. Downloaded asset URLs, identities and SHA256 values must agree with release metadata and
the signed manifest, and the existing distribution checks run on the downloaded bytes.

The tag-only manual dispatcher retains required source confidentiality and runtime license checks. Its isolated
upload job retains only OIDC authority, no checkout, dependency installation or build, and rechecks the immutable
manifest and exact distribution hashes before the unchanged PyPA trusted publisher. Missing producer manifests,
unsuccessful producers and old unsigned releases refuse; there is no rebuilding fallback or existing-file skip.

### Consequences

The duplicated PyPI notifier and distribution builds disappear. Existing releases without the signed producer
manifest cannot use this route. The coordinator must await the original producer's terminal success before dispatch;
release existence alone is insufficient. Final released-source alignment and observed queue/build timings remain
publication requirements; this source change alone makes no release-duration claim.

### Confirmation

Owning unit and integration tests reject incorrect source, producer, invocation, provenance, missing or substituted
assets, changed bytes and incomplete packages. Workflow guards enforce retained confidentiality/licenses and the
separate upload authority; upload-time tests execute the exact inline verifier against tampering.
