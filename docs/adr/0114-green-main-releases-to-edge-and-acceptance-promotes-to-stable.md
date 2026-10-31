---
status: proposed
date: 2026-10-31
decision-makers: maintainer
amends: 0013, 0079, 0110
amends-0013: release pull requests can merge without a person
amends-0079: reviewed workflow code can dispatch the upload
amends-0110: discovery stays stable, and edge is opt-in
related: 0008, 0031, 0085, 0094, 0096
---

# 0114: Green main releases to edge, and acceptance promotes to stable

## Context and Problem Statement

A release of 0.9.9 took these manual steps: squash merge, release pull request, checks, tag run, `pypi.yml` dispatch,
`compose.yml` dispatch, verification, public acceptance. That limits delivery to about 3.5 releases a day, while the
maintainer wants every green merge released, about 35 a day. Publishing everything to the place `uvx heinzel@latest`
reads is not possible: a PyPI file can never be replaced or reused, even after deletion
([PyPI](https://discuss.python.org/t/delete-an-existing-project-on-pypi-and-then-create-one-with-the-same-name-but-got-error-the-filename-has-already-been-used/11628)),
and a project holds [10 GB by default](https://docs.pypi.org/project-management/storage-limits). One release is 11 MB
(2.5 MB wheel, 8.5 MB source distribution), and the project already uses 283 MB. Every merge on PyPI would fill it in
about 25 days. How do we release every green merge, keep users on what was accepted, and keep the trust chain?

## Decision Drivers

- No human step between a green `main` and an edge release, or between passing acceptance and stable.
- No weaker trust: pinned actions, provenance, trusted publishing and protections stay as they are.
- Irreversible publishing only from reviewed workflow code, and only of bytes that passed acceptance.
- Every default install path stays versionless and on stable. Edge is reachable only by an explicit choice.
- Automation can merge dark and be switched on later, without disturbing the current release path.

## Considered Options

- **Edge as GitHub pre-releases, stable on PyPI after acceptance** (chosen)
- **PEP 440 dev versions on PyPI for every merge**: uv does not select them without opt-in (checked with uv 0.13.5:
  `uvx pkg@latest` takes 1.0.0 over 1.1.0.dev5, `--prerelease allow` takes the dev version), so it would be safe for
  users. But about 385 MB a day fills the project in about 25 days, with filenames that can never be reused.
- **Release candidates (`X.Y.ZrcN`) on PyPI before each stable**: costs a further 11 MB per attempt, and the accepted
  bytes are not the bytes that ship, because the version inside the wheel differs.
- **A release pull request for every merge**: keeps the changelog on `main` for every release, but doubles merges and
  CI, and caps throughput at one release per CI run.
- **Merge queue**: solves up-to-date tests, which strict mode being off already stopped paying for. Main CI gates a
  release either way, so the queue adds a settings migration and no release safety.

## Decision Outcome

Chosen option: **edge as GitHub pre-releases, stable on PyPI after acceptance**, because it is the only option that
releases every merge, avoids filling PyPI, and ships exactly the bytes that were accepted.

**Gate.** `CI OK` on the exact `main` commit gates a release. Strict up-to-date stays off: a pull request's CI is early
feedback, and a semantic conflict between two merges turns `main` red and blocks the release until it is fixed forward.
The `update-branch` loop in `update-prs.yml` no longer has a purpose and goes. A failed `main` run is retried once,
failed jobs only, by reviewed workflow code. No deadline or assertion changes.

**Edge, on every releasable merge.**

- The workflow runs from the default branch's tip, and only when that tip is the commit that just went green. A commit
  that `main` has already passed is released with the next one. A dispatch on `main` publishes one edge release, to
  prove the channel before it is activated.
- A merge is releasable unless every changed path is non-shipping: `docs/`, `tests/`, `.github/`, `.claude/`, Markdown
  at the root, `mkdocs.yml`. Unknown paths count as releasable. GitHub Pages stays independent of both channels.
- The version is `<next>.dev<N>`, where `<next>` comes from the existing conventional-commit calculation and `<N>` is
  the number of commits on `main`, so it only grows. No commit goes to `main`: the version is stamped in the build
  checkout, and the changelog goes in the release notes.
- The tag is `edge-v<version>`, outside the `v*` namespace, so `pypi.yml`, the `pypi` environment and the stable
  tooling cannot select it.
- The GitHub release is a pre-release with `latest` off. It carries the wheel, source distribution, SBOM and build
  provenance, built by the same steps as stable.
- Nothing goes to PyPI. A rolling pointer asset names the newest edge release and its digest, so the opt-in path needs
  no GitHub API quota. The pointer selects; the immutable release's digests verify.

**Stable, promoted only by acceptance.**

1. An edge commit passes acceptance, and the promotion throttle and PyPI budget allow a promotion.
2. Reviewed workflow code opens `chore(release): vX.Y.Z`, with the version bump and changelog, and merges it on green.
   This keeps [0013](0013-pull-request-based-development-and-releases.md): `main` changes only through a pull request
   with `CI OK`. A manual release through `prepare-release.yml` stays available and still waits for a maintainer.
3. **Release tag** creates the tag, and **Publish** creates a GitHub release held as a pre-release with `latest` off.
4. Acceptance runs against those exact final-version bytes, downloaded from the release, on hosted macOS and Linux.
5. On a pass, the release becomes final and `pypi.yml` is dispatched on the tag. Its isolation, tag checks and manifest
   stay as [0079](0079-core-pypi-trusted-publishing-is-isolated-from-builds.md) set them, apart from that automatic
   dispatch. It also refuses a tag without a passing acceptance receipt for the same commit and digests.
6. The composer picks up the new core as today and makes `app-vX.Y.Z` the latest app.
7. The literal public commands run as a final check. They cannot gate an upload that has already happened.

**What is irreversible, and how we avoid it.** PyPI files and filenames are permanent, and yanking needs a maintainer
login. So PyPI receives only final versions that have passed acceptance, at most one per six hours by default, and
promotion stops when the project exceeds 80% of its limit. At four a day, 10 GB lasts about 220 days. Trimming the
source distribution and asking PyPI for a larger limit are the follow-ups. Git tags and GitHub releases can be removed,
and an edge release can be left behind, so a bad edge costs nothing to skip.

**Rollback.** Before upload, rollback is not promoting. After upload, the fix is publishing the next version. A failed
post-publish check stops further promotion and opens an issue. Re-designating the previous app as latest is possible,
because the launcher selects the app and not the other way round, but it is left out until needed.

**Default is stable.** `uvx heinzel@latest`, the curl installer, the app's update prompt and `hz system update` take no
channel and resolve to stable. App discovery follows the `releases/latest` redirect, which never returns a pre-release,
and also refuses any pre-release version it is handed. The acceptance harness and unit tests prove the default never
selects edge.

**Switching channels, at any time, in both directions.**

- The setting `update_channel` takes `stable`, `edge` or `main`. The old value `release` still works and means
  `stable`. The choice lives in configuration, which survives updates and reinstalls.
- `hz system channel` shows the current channel and what a switch would do, and changes nothing without `--yes`.
  Settings shows the same statement before the change is applied.
- Stable to edge installs the newest edge release on the next update.
- Edge to stable never downgrades. The installed edge stays until stable is at least as new, then the next update moves
  to stable. Because the version is `<next>.dev<N>` and `<next>` only grows, the first stable at or above an installed
  edge contains all of its commits, so its state schemas are never older than what the edge wrote.
- To keep that guarantee, a state schema that reached `main` is never reverted; it is fixed forward. A reader that
  meets a newer schema keeps refusing it, as [`hz` contracts](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md)
  do today. No downgrade is offered: a downgrade would need reverse migrations nobody could test for every pair.

**Composition.** The composer also composes edge from the core's pointer, on its existing schedule, so merges batch
naturally. `app-v<dev version>` releases are pre-releases with `latest` off. Only `app-vX.Y.Z` can be latest.

**Plugins follow on merge.** app-v0.9.9 failed to compose because a documentation pull request raised a plugin's
version and no release of that version ever existed, which showed only at release time. So plugin CI refuses a version
bump in a pull request that changes nothing the plugin ships. After a green `main`, reviewed workflow code in the
plugins repository releases every plugin whose version has no tag yet, through its existing **Release one plugin**
workflow. The same `RELEASE_CHANNELS` activation applies, because that workflow can upload to PyPI.

**Acceptance** moves to this public repository as a workflow on hosted macOS and Linux runners, with no private
material and no credentials. Cases needing a private plugin or a real daemon stay UNPROVABLE, and promotion accepts
that result only for those named cases.

**Source review** becomes one command with risk tiers: full for the installer, authentication, approvals and release;
none or light for `docs/` and `tests/`. The trusted guard stays on a protected ref and verifies the live pull request
head and base at run time. It never executes target code.

**Feature flags.** Big work merges dark behind a named flag, off by default: a registry in code, the setting
`features`, and `HZ_FEATURES`. Unknown names are reported and ignored. Existing `disabled_modules` stays for whole
modules. A flag does not hide state schema changes; those follow the rule above.

**Activation.** The variable `RELEASE_CHANNELS` is unset by default, which runs nothing that publishes. `edge` turns on
edge releases, and `stable` also turns on promotion. Only a maintainer sets it. Until then the manual path keeps
working unchanged.

### Consequences

- Good: no human step on the way to edge or stable, with roughly 35 edge releases a day and stable bounded by budget.
- Good: the accepted bytes are the shipped bytes, and PyPI holds only those.
- Good: users are on stable unless they choose otherwise, and can leave edge safely.
- Bad: someone leaving edge can wait for stable to catch up, up to the promotion cadence.
- Bad: `CHANGELOG.md` on `main` changes only at stable; edge changes are in the release notes.
- Bad: a window exists between a release becoming final and its PyPI upload. A failed upload leaves GitHub ahead of
  PyPI, which is detected and retried by the same reviewed workflow, not by hand.
- Bad: `main` was red in 33 of 67 runs in the last fortnight. Until that improves, edge will skip commits. It releases
  only green commits and does not lower the bar.
- Bad: hosted acceptance covers a fresh machine, not a real MacBook or a live daemon handover. The human's validation
  from a user's seat stays separate.

### Confirmation

`tests/unit/ci_test.py` and `tests/unit/release_channel_test.py` pin the workflow triggers, gates and tag namespaces,
and the releasable and version rules. `tests/unit/pypi_release_test.py` keeps the upload refusals. Update and install
tests prove that the default never selects edge, that switching works both ways, and that edge never downgrades. The
acceptance workflow's result file is the receipt that promotion and `pypi.yml` check.

## More Information

Sources: [PyPI storage limits](https://docs.pypi.org/project-management/storage-limits),
[uv pre-release handling](https://docs.astral.sh/uv/concepts/resolution/#pre-releases),
[PyPI trusted publishers](https://docs.pypi.org/trusted-publishers/security-model/).
