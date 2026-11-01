---
status: proposed
date: 2026-10-29
implementation: built
related: 0007, 0026, 0094, 0095, 0096
implemented-by: 270
---

# 0097: Plugin authors use one small artifact-based toolchain

## Context and Problem Statement

The plugin boundary makes independent delivery possible, but a fresh-author review found a large default starter,
conflicting standalone and collection commands, incomplete agent instructions, and a missing setup dependency.
The release pipeline also rebuilt wheels after staging publication files. The maintainer requires a tested,
pleasant journey from reading the guide to publishing a useful plugin, with minimal authored boilerplate.

## Considered Options

- **A minimal starter and one independently versioned developer toolkit** (chosen).
- Keep copying another plugin's configuration: leaves undocumented assembly work and drifts across repositories.
- Make every plugin inherit the complete core repository tooling: increases setup and maintenance without testing
  the plugin's own behavior more effectively.

## Decision Outcome

Chosen option: **a minimal starter and one independently versioned developer toolkit**, because authors should work
on their feature while common commands enforce the delivery contracts.

- A default starter contains one typed operation and one public-contract test. A contributions preset retains the
  complete status, navigation, Today, notification and local-view example. Existing public lets remain unchanged.
- Every layout supplies concise human and agent guides and the same small owning Makefile. Standalone layout
  includes CI/release workflows; a collection reuses its host's generic workflow, never initializes an embedded Git
  repository and never copies parent governance. Owning commands also work in a host whose root builds core.
- Host hooks select changed plugin projects and public-contract consumers from metadata, without invoking core's
  tests for plugin-only changes. Core and contrib expose the same generic per-plugin protected release workflow;
  adding a project needs no parent registration or workflow edit.
- Core-root pull-request preflight and agent completion checks use that same scope selection. Plugin-only PRs
  retain shared commit, source confidentiality and credential policies, then run owning checks, build, audit and wheel acceptance;
  mixed/core changes retain core preflight. Initial pushes and deletion-only edits remain covered.
- Optional source plugins in Core use the main-restricted `plugin-pypi` environment. The tagged core publisher
  retains its existing `pypi` tag policy; other plugin hosts use their main-restricted `pypi` environment.
- Scaffolding supports explicit defaults for unattended agents. Derived names do not require repeated answers.
  Generated compatibility ranges respect pre-1.0 minor boundaries. Candidate wheels remain explicit overrides.
- The generic developer toolkit is an ordinary development artifact in `heinzel-plugins`, with no plugin entry
  points or core runtime dependency. It is independently built, checked and released as a versioned GitHub wheel.
  Generated projects consume that artifact, never core implementation sources or other plugin implementations.
  Minimal core does not install it.
- The same selected commands cover setup, a fast owning loop, full owning checks, locked dependencies, preview,
  build, installed-wheel acceptance, release preview and explicit protected publication. Shared-tooling-only checks
  have their own name. Missing prerequisites and invalid names fail with the next actionable command.
- Human, AGENTS.md and CLAUDE.md instructions agree. The minimal path does not copy the complete reviewer stack.
  Retained repository templates test their actual setup command; new authors do not inherit the core reviewer stack.
- Generated dependency locks are validated by the dependency manager when committed and in CI. The retained
  template's exact `uv.lock` path uses that check instead of the generic added-file size check; ordinary files keep
  the existing size limit. Adding valid dependencies must not make a fresh scaffold's first commit impossible.
- Acceptance can consume an explicit prebuilt wheel. Publication stages and attests those exact tested bytes.
  Standard app composition consumes independently released, verified plugin wheels rather than rebuilding them.
- An upload-only release path verifies the original tag, source commit, workflow provenance and artifact digests
  before later PyPI publication. It runs in the existing protected publishing workflow, without rebuilding or
  selecting an unrelated newer main commit. Local release preview does not publish directly.
- No additional core module is extracted. PyPI account authentication remains an external prerequisite; no
  persistent publishing token or broader grant is introduced.
- Public and private standalone projects share the same local commands. Repository visibility selects a private
  GitHub release or public registry; a private source cannot be sent to PyPI. Private CI keeps advisory queries
  local or uses the owning project's internal audit command. Private attestations are optional where the GitHub
  plan supports them; public releases and deferred public-registry uploads retain mandatory provenance checks.

### Consequences

- Good: a new author edits a small feature and test, while standalone and collection plugins share the same checks.
- Good: plugin tooling can improve independently, and tested artifacts retain their identity through composition
  and deferred registry publication.
- Bad: the toolkit becomes another versioned development artifact, and its compatible template pin must be tested.
- Bad: rich examples and both layouts require explicit journey coverage rather than only render checks.

### Confirmation

Disposable author journeys exercise both layouts against an installed core wheel: scaffold, setup, add an
operation, fast/full checks, build, installed CLI/API behavior and release preview. Tests assert that collection
generation changes only its new directory, creates no embedded repository, and selects no core build/test jobs.
The contributions preset's setup is exercised on a fresh environment. Publisher tests reject changed artifacts,
wrong tags, unverified provenance and a resume that selects a newer source commit. Composition checks install the
verified four-default wheel set and preserve explicit disable/removal choices. Timings and selected work remain
visible; network bootstrap measurements are distinguished from warm author feedback.
