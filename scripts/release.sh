#!/usr/bin/env bash
# Prepare a release as a pull request: derive the next semantic version from conventional commits (or force a bump),
# update the version everywhere bump-my-version knows about, regenerate CHANGELOG.md (keeping the hand-written
# introductions of earlier releases), and commit "chore(release): vX.Y.Z" on a new branch `release/vX.Y.Z`.
#
# Usage: INTRO="prose" bash scripts/release.sh [auto|patch|minor|major] [--open-pr]
#   INTRO      required: the new release's introduction, written under its heading in CHANGELOG.md and the release notes;
#              edit it in the release pull request like the rest.
#   --open-pr  pushes the branch and opens the release pull request with gh. Merging it tags and publishes the
#              release (workflow "Release tag"). Prints the tag on the last line.
set -euo pipefail

bump="${1:-auto}"
open_pr="${2:-}"
die() { echo "error: $*" >&2; exit 1; }

[[ "${INTRO:-}" =~ [^[:space:]] ]] || die 'INTRO is required: summarize what changed for users, e.g. make release INTRO="Clearer settings and improved search."'
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[[ "$bump" =~ ^(auto|patch|minor|major)$ ]] || die "bump must be auto, patch, minor or major, not '$bump'"
[[ -z "$(git status --porcelain)" ]] || die "working tree is not clean"
[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || die "releases are prepared from main"

git fetch --tags --quiet origin 2>/dev/null || true

current="$(uv run --frozen --no-sync --no-build bump-my-version show current_version)"
if [[ "$bump" == "auto" ]]; then
  next="$(uv run --frozen --no-sync --no-build git-cliff --offline --bumped-version)"
  released="$(git tag --merged HEAD --list 'v[0-9]*')"
  next="$(uv run --frozen --no-sync --no-build python scripts/release_version.py "$current" "${next#v}" "$released")"
else
  next="$(uv run --frozen --no-sync --no-build bump-my-version show --increment "$bump" new_version)"
fi
next="${next#v}"
tag="v$next"

[[ "$next" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "could not determine the next version (got '$next')"
if [[ "$next" == "$current" ]] && git rev-parse -q --verify "refs/tags/v$current" >/dev/null; then
  die "no releasable commits since v$current"
fi
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && die "tag $tag already exists"

echo "Preparing $tag (was $current)" >&2
git switch --quiet --create "release/$tag"
if [[ "$next" != "$current" ]]; then
  uv run --frozen --no-sync --no-build bump-my-version bump --new-version "$next" --no-commit --no-tag --allow-dirty >&2
fi
uv lock --quiet
# Regenerate what derives from the version (the plugin template's pyproject, generated docs); the release PR's
# lint fails otherwise.
make --no-print-directory fix >&2
# Keep published sections verbatim: rebuilding them from git history can drop entries after history is rewritten.
if [[ -f CHANGELOG.md ]]; then cp CHANGELOG.md "$work/previous.md"; fi
uv run --frozen --no-sync --no-build git-cliff --offline --tag "$tag" --output CHANGELOG.md
uv run --frozen --no-sync --no-build python scripts/changelog_intros.py "$work/previous.md" CHANGELOG.md --version "$next" \
  --intro "${INTRO:-}"
git add --update
git commit --quiet --message "chore(release): $tag"

if [[ "$open_pr" == "--open-pr" ]]; then
  git ls-remote --exit-code --heads origin "release/$tag" >/dev/null && die "release/$tag already exists on origin"
  git push --quiet --set-upstream origin "release/$tag"
  uv run --frozen --no-sync --no-build python scripts/release_notes.py "$tag" > "$work/notes.md"
  gh pr create --base main --head "release/$tag" --title "chore(release): $tag" --body-file "$work/notes.md" >&2
fi
echo "$tag"
