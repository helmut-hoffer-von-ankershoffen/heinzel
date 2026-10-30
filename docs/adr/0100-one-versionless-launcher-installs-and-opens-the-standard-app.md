---
status: proposed
date: 2026-10-30
decision-makers: maintainer
amends: 0094
related: 0097, 0098
---

# 0100: One versionless launcher installs and opens the standard app

## Context and Problem Statement

The maintainer requires installation to be a breeze: no version numbers, plugin lists or conflicting instructions.
The released bootstrap SDK looked for an app profile with its own version, although SDK and app composition ship
independently. A newer SDK therefore broke the documented installer when its matching composition was absent.
The PyPI description also reused repository-relative artwork and links that do not resolve on PyPI.

## Considered Options

- **Use `uvx heinzel@latest` as one versionless install-and-open launcher** (chosen).
- Ask people to pin a complete app version or enumerate plugin dependencies: exposes release mechanics in onboarding.
- Couple every SDK release to rebuilding every plugin: contradicts independent delivery and slows development.

## Decision Outcome

Chosen option: **one versionless launcher over verified, independently published app compositions**, because people
should choose the product rather than coordinate its distributions.

- Bare `uvx heinzel@latest` installs the newest complete standard app when a complete managed app is absent, then opens
  its foreground web workspace. Later launches use the installed app without registry or provider discovery.
  Ctrl-C stops it; no daemon, login item or sharing is enabled by installation.
- Runtime arguments are delegated to that same managed app so the UI and CLI see the same plugins. Informational
  help/version flags remain available without installation. `hz` remains the independently usable SDK/CLI entry point;
  `hz-install` remains the lower-level installer for automation and explicit candidate profiles.
- The documented command uses `@latest` to refresh a stale bootstrap cache or bypass an older installed SDK.
  It names no release number and selects the same complete app profile; plain `uvx heinzel` remains supported
  after a current launcher is installed. This follows [uv's documented tool version behavior](https://docs.astral.sh/uv/concepts/tools/).
- An optional POSIX `curl`/`wget` installer supports macOS and Linux. It reuses existing uv or installs uv through
  Astral's official HTTPS installer, then invokes the same versionless launcher. It needs no administrator access
  or preinstalled Python. Windows may work independently but is unsupported. The script is served with the docs;
  README, PyPI and Pages share its command and link to its inspectable source.
- The installer discovers app-scoped release references and skips only not-yet-published profiles. It never infers
  plugin names from Core code. Explicit profiles remain exact; schema, identity, size and hash checks still fail closed.
- A successful managed install records its verified composition privately. The launcher checks required distribution
  metadata without importing plugins, honors explicit removals, and replaces itself with the managed executable.
  Managed binaries lead the child PATH so a daemon or subprocess never retains the temporary bootstrap path.
- The plugin repository composes and verifies app profiles against released SDK artifacts independently. A scheduled
  check publishes missing compositions without rebuilding provider artifacts or running Core's test suite.
- README, Pages and PyPI take their primary command from one checked source. Public onboarding never asks people
  to supply versions or enumerate defaults. PyPI uses a dedicated portable description with absolute links and
  raster exports of the actual project artwork.
- A repository URL can select its single metadata-declared plugin project in a virtual workspace. Collection
  discovery reads bounded metadata at one immutable commit; GitHub-resolved links stay within that repository and
  commit, and ambiguous collections require a distribution name.
  Adding a plugin verifies fresh installed discovery before recording success, including when the package installer
  exits successfully without installing a plugin.

### Consequences

- Good: first use is one command, defaults are automatic, and SDK/plugin development remains independent.
- Bad: app release discovery, a managed-install receipt and the launcher are additional generic boundaries to maintain.

### Confirmation

`tests/unit/heads/install_test.py` covers missing profiles, release ordering, explicit overrides and failed verification.
`tests/unit/heads/bootstrap_test.py` covers first use, existing/offline use, incomplete SDK installs and CLI compatibility.
`tests/unit/pypi_description_test.py` checks the one-command contract and portable artwork.
`tests/integration/shell_installer_test.py` exercises prerequisite installation and failure cleanup without network.
The plugin repository's composition tests cover scheduled no-op and new SDK selection. Release acceptance exercises a fresh versionless
installation and subsequent launch with independently verified default plugin wheels.
