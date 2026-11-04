---
status: proposed
date: 2026-11-04
implementation: built
decision-makers: maintainer
related: 0011, 0036, 0039
---

# 0125: Prepare locked dependencies before isolated local builds

## Context and Problem Statement

A wheel build does not prepare Hatchling's additional editable-install requirement, `editables`. A later
`uv run pytest` can therefore create its controller environment and resolve an uncached build requirement even
when the wheel and runtime dependencies are cached. A locked application environment alone does not constrain
PEP 517's separate build environment.

## Considered Options

- **Prepare a locked build group and use its exported hashes in isolated builds** (chosen).
- **Disable build isolation**: uses the controller's installed packages instead of a separate build environment.
- **Retry the registry request**: retains mutable backend resolution on every cold controller build.

## Decision Outcome

Chosen option: **prepare a locked build group and use its exported hashes in isolated builds**, because the same
lock supplies both downloaded artifacts and build constraints without changing installed application receipts.

- The build group contains Hatchling, its editable helper and the Node wheel required by the core's web build hook.
  These are development dependencies and never become runtime requirements.
- Setup first installs exported build requirements, with exact versions and required hashes, into a disposable
  environment to prepare resolver metadata and wheel bytes. It then installs all locked controller groups without
  building the project and uses `uv pip install --no-deps --editable` in isolation, offline, with the exported
  versions and hashes required for every backend dependency. A missing artifact or hash fails the build.
- End-to-end preparation performs the same controller bootstrap before building the wheel. Subsequent local
  wheel builds use the prepared cache and the same temporary hash constraints. Public cold-install journeys keep
  their real registry access and ordinary tool receipts.
- Constraints are scoped to these subprocesses; no index, receipt, build-isolation or application policy changes.

### Consequences

- Good: wheel and editable builds use the same reviewed backend artifacts, including transitive dependencies.
- Good: missing build artifacts fail before test procedure deadlines, with network disabled for the build itself.
- Bad: initial preparation still needs the registry when the lock's artifacts are absent from the cache.

### Confirmation

A real isolated-build regression prepares backend wheels in a private cache, removes registry access and checks
both the editable controller and wheel output. It also requires failure with an incomplete cache or wrong hash;
focused tests check that preparation and builds retain isolation and scoped constraints.

## More Information

[uv build constraints](https://docs.astral.sh/uv/concepts/projects/build/#build-constraints) describe the separate
backend constraints and hash verification used here.
