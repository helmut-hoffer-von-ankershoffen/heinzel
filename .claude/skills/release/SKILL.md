---
name: release
description: Cut a Heinzelmännchen release - semantic version from conventional commits, changelog, tag, GitHub release with provenance. Use when asked to release, publish or tag a version.
disable-model-invocation: true
---

# Release

Releases are cut from `main` by automation; never edit versions or `CHANGELOG.md` by hand.

1. Make sure `main` is green in CI and up to date locally.
2. Preview: `make changelog` shows unreleased entries; `uv run git-cliff --bumped-version` the next version.
3. Open the release pull request: `make release INTRO="What changed for users."` (`BUMP=minor` for a minor release) or Actions → **Prepare release**
   → Run workflow (bump `auto`, or force `patch`/`minor`/`major`): a GitHub runner prepares it, nothing runs locally
   (`make release-local INTRO="What changed for users."` is the local path). Every release requires an
   introduction: a few sentences on what changed for users, as the **intro** input or `INTRO="..."`.
4. Review the pull request `chore(release): vX.Y.Z` (version bumps, CHANGELOG.md, the introduction under the new
   heading; introductions of earlier releases are kept); CI runs on it and it auto-merges when green.
5. **Release tag** tags the merge commit and **Publish** builds, attests and creates the GitHub release:
   `gh run watch`.
6. Verify: `uv tool install --force git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel@vX.Y.Z`, then
   `hz --version` and `hz system changelog`.
