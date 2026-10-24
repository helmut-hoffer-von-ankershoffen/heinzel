---
name: release
description: Cut a Heinzelmännchen release - semantic version from conventional commits, changelog, tag, GitHub release with provenance. Use when asked to release, publish or tag a version.
disable-model-invocation: true
---

# Release

Releases are cut from `main` by automation; never edit versions or `CHANGELOG.md` by hand.

1. Make sure `main` is green in CI and up to date locally.
2. Preview: `make changelog` shows unreleased entries; `uv run git-cliff --bumped-version` the next version.
3. Open the release pull request: Actions → **Release** → Run workflow (bump `auto`, or force `patch`/`minor`/
   `major`), or `make release` (`make release BUMP=minor`) from an up-to-date `main`.
4. Review the pull request `chore(release): vX.Y.Z` (version bumps, CHANGELOG.md); CI runs on it. Merge it.
5. **Release tag** tags the merge commit and **Publish** builds, attests and creates the GitHub release:
   `gh run watch`.
6. Verify: `uv tool install --force git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel@vX.Y.Z`, then
   `hz --version` and `hz system changelog`.
