---
status: accepted
date: 2026-10-23
amended-by: 0013, 0079
---

# 0008: Releases are derived from conventional commits

## Context and Problem Statement

Releases should be boring, frequent and identical whether a person or an agent cuts them.

## Considered Options

- **Conventional commits, git-cliff and a release script** (chosen)
- **python-semantic-release**: more magic, less control over the changelog format.
- **Release Please**: PR-based flow adds a step for a small project.

## Decision Outcome

Chosen option: **versions and changelog derived from conventional commits with git-cliff**, because nobody edits a
version or changelog by hand.

- Commits follow Conventional Commits, checked by a commit-msg hook (commitizen) and in CI.
- `scripts/release.sh` computes the next SemVer version with `git-cliff --bumped-version` (or a forced
  patch/minor/major), updates `pyproject.toml` and `copier.yml` with bump-my-version, regenerates `CHANGELOG.md`
  with git-cliff, commits `chore(release): vX.Y.Z` and creates an annotated tag.
- The **Release** workflow runs CI, then the script, pushes commit and tag, then calls **Publish**. Publish (also
  triggered by tags pushed from a workstation) checks that the tag matches the version, builds wheel and sdist,
  attests build provenance and creates the GitHub release with git-cliff notes.
- The changelog ships inside the wheel; `hz system changelog` shows it.

### Consequences

- Good: no hand-edited versions or changelogs.
- Bad: the workflow pushes to `main` with the GitHub token; branch protection must allow the Actions bot to push,
  or releases are cut with `make release` by a maintainer.

### Confirmation

The commitizen commit-msg hook (`.pre-commit-config.yaml`), the CI job "Conventional commits" and the `commits`
step of `make preflight`; the CI "Build" job checks that the wheel ships `CHANGELOG.md`.

## More Information

Amended by [0013](0013-pull-request-based-development-and-releases.md): the release is a pull request, and tagging
is done by the Release tag workflow.
