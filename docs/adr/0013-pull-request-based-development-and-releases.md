---
status: accepted
date: 2026-10-23
implementation: built
amends: 0008
amended-by: 0114
---

# 0013: Pull-request-based development and releases

## Context and Problem Statement

Every change to `main` — features, dependency updates and releases — should be reviewed and checked the same way.
Pushing release commits directly to `main` from a workflow bypasses that.

## Considered Options

- **A ruleset on `main`; releases are pull requests** (chosen)
- **Release workflow pushing to `main` with a bypass**: invisible changes to a protected branch.
- **release-please**: same flow, but a separate configuration and changelog generator next to git-cliff.

## Decision Outcome

Chosen option: **pull-request-based development and releases**, because then `main` never changes without review
and CI. This supersedes the push-to-main part of [0008](0008-release-from-conventional-commits.md).

- A repository ruleset protects `main`: changes only through pull requests, the `CI OK` check must pass, linear
  history (squash merges), no force pushes or deletion. Pull request titles are conventional commits and become the
  squash commit message.
- Releases are pull requests. The **Release** workflow derives the next version, commits the version bump and
  changelog on `release/vX.Y.Z`, opens the pull request and dispatches CI on it (pull requests opened with the
  GitHub token don't trigger workflows by themselves). Merging it triggers **Release tag**, which tags the merge
  commit and calls **Publish**.

### Consequences

- Good: a release is reviewed like any change, and its changelog can be edited in the pull request before merging.
- Bad: two clicks per release (run, merge) instead of one; in exchange `main` never changes without CI.
- Bad: repository settings (ruleset, squash-only merges, Actions allowed to open pull requests) are part of the
  setup; CONTRIBUTING.md documents them.

### Confirmation

The `CI OK` job in `.github/workflows/ci.yml` aggregates the required jobs; the ruleset itself lives in the GitHub
repository settings, as documented in CONTRIBUTING.md, not in this repository.
