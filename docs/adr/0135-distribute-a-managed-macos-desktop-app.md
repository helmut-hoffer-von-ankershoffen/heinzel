---
status: proposed
date: 2026-11-06
implementation: planned
amends: 0078
amends-0078: publish a macOS desktop bundle without Developer ID
related: 0116, 0134
---

# 0135: Distribute a managed macOS desktop app

## Context and Problem Statement

The Electron shell can be built in CI. A Mac user needs to install, update and remove that shell from the published
release with the Heinzel CLI. Requiring a source checkout and JavaScript build makes this impractical.

## Considered Options

- Publish a protected macOS ARM64 release archive without Developer ID signing or notarization (chosen).
- Publish a notarized app. This requires signing credentials and a separate release process.

## Decision Outcome

Chosen option: **publish a protected macOS ARM64 release archive**, because it supports installation from the release
using the existing protected build and provenance infrastructure.

- Bind the archive to an official protected source and version with a separate attested desktop manifest. Keep
  Python registry promotion tied to its existing manifest.
- Verify provenance and archive identity before extracting to private staging. Install the verified app into the
  user's Applications directory and use an ownership receipt to guard replacement and removal.
- Keep the bundle immutable and bind the installed CLI separately. Launch it by absolute path from Finder.
- Keep macOS Gatekeeper and quarantine in effect. First launch may require the user's per-app approval.
- Automatic update manages an already installed desktop bundle. Stage and verify before replacing it, defer
  replacement while the bundle is in use, and preserve the existing installation on failure.
- Uninstall removes the owned bundle and its binding while preserving the user's Heinzel profile.

### Consequences

The same artifact can be installed without a source checkout. The macOS trust prompt remains part of first launch.

### Confirmation

Verify the release archive and provenance in the protected publishing workflow. Exercise install, Finder launch,
update and rollback, and uninstall with the same release archive in an isolated macOS profile before release.
