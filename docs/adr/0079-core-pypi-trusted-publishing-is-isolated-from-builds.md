---
status: proposed
date: 2026-10-25
amends: 0008
related: 0013, 0017, 0043
implemented-by: 188
---

# 0079: Core PyPI trusted publishing is isolated from builds

## Context and Problem Statement

A normal registry installation should use a reviewed release with the same complete browser app and macOS notifier
as the GitHub distribution. PyPI accepts short-lived GitHub OIDC credentials through a
[trusted publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/); that identity does not establish that
an artifact is safe. Build tooling must never receive the permission to mint publishing credentials.

## Considered Options

- **Core-only trusted publishing, with unprivileged builds and a separate upload job** (chosen)
- **A saved PyPI API token in the build job**: long-lived credentials would be exposed to package builds.
- **Publish every workspace plugin immediately**: their publisher registration and clean-install acceptance are separate work.

## Decision Outcome

Chosen option: **an explicitly dispatched, tag-only core publisher with isolated jobs**, because an irreversible
registry upload must consume a genuine reviewed release without granting its build tooling upload authority.

- `.github/workflows/pypi.yml` has no arbitrary source, version, registry or dry-run input. A maintainer dispatches
  it on an exact `vX.Y.Z` tag only after the release has been reviewed and published on GitHub.
- Checkout names the full tag reference; its peeled commit must be HEAD, belong to `origin/main`, carry the release
  commit title and match the core project name/version. A branch with the same name cannot substitute for the tag.
- macOS builds the notifier without OIDC. Linux builds only the core wheel and source distribution, with the built
  browser app and matching notifier. Source/artifact identity, metadata and required packaged files are checked.
- The upload job checks the immutable artifact manifest, checks out no project source, runs no package build,
  requests only `id-token: write`, and uses the `pypi` environment. PyPA's action publishes with attestations;
  passwords, alternate registries and silent skipping of existing files are not supported.
- The registered publisher is the core repository's `pypi.yml` workflow and `pypi` environment. GitHub's environment
  policy allows version tags only. A maintainer's final upload authorization is separate from preparing this code.
- Core badges replace the redundant GitHub release badge with PyPI within the existing ten-badge limit. Unpublished
  plugin templates retain their GitHub release badges. Installation instructions keep a working Git source until
  the first registry release is verified; a browser app does not imply a packaged desktop installer.

### Consequences

- Good: no stored registry secret, no upload authority in dependency/build steps, and no accidental plugin publication.
- Bad: publishing repeats the platform build and is an explicit extra release step. A failed or partial upload needs
  reconciliation against PyPI's immutable files, never a blind `skip-existing` retry.

### Confirmation

Source and artifact tests exercise exact-tag identity, main ancestry, release identity, package metadata, missing
browser/notifier payloads, unexpected files and hash corruption. Workflow policy tests enforce permission separation,
fixed publisher/action inputs and tag-only dispatch. `make lint` checks actionlint, zizmor and generated badges.
A real reviewed release still requires the clean registry install and UI proof in [PUBLISHING](../PUBLISHING.md).
