#!/usr/bin/env bash
# Open (or update) the pull request for this branch: preflight, push, PR to main, squash auto-merge.
# Usage: bash scripts/pr.sh "feat(scope): title"   BODY="..." sets the description; NO_PREFLIGHT=1 skips make preflight;
# DRAFT=1 opens a draft pull request and leaves auto-merge off, for proposals a maintainer decides on.
# Pull requests always target main: stacked pull requests merge into their base branch, not into main.
# From a fork (a remote named `upstream` next to your `origin`), the branch goes to your fork and the pull request
# to upstream's main; a maintainer turns on auto-merge after review.
set -euo pipefail

title="${1:-}"
[ -n "$title" ] || { echo 'Usage: make pr TITLE="feat(scope): what changes"' >&2; exit 2; }
branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" != main ] || { echo "On main; create a branch first: git switch -c feat/short-name" >&2; exit 2; }
[ -z "$(git status --porcelain)" ] || { echo "Uncommitted changes; commit them first." >&2; exit 2; }
PATH="$(cd "$(dirname "$0")/.." && pwd)/.tools/bin:$PATH"  # the pinned gh, also when run without make
command -v gh >/dev/null || { echo "make pr needs gh; run make install (make doctor shows what is missing)." >&2; exit 2; }

# owner/name: the last two path parts of a remote URL, whatever the host or alias (https, ssh, a mirror).
slug() { git remote get-url "$1" | sed -E 's#/+$##; s#\.git$##; s#^.*[:/]([^/:]+/[^/:]+)$#\1#'; }
base=origin
owner="$(slug origin | cut -d/ -f1)"
repo="$(slug origin)"
if git remote | grep -qx upstream; then
  base=upstream
  repo="$(slug upstream)"
fi

uv run --quiet cz check --message "$title" >/dev/null
uv run --quiet --script scripts/check_confidentiality.py --text "$title"
uv run --quiet --script scripts/check_confidentiality.py --branch
git fetch --quiet "$base" main
git merge-base --is-ancestor "$base/main" HEAD || { echo "Behind $base/main; run make sync first." >&2; exit 2; }
[ -n "${NO_PREFLIGHT:-}" ] || make --no-print-directory preflight BASE="$base/main"

# The update-prs workflow may have merged main into the remote branch; build on top of that instead of rejecting.
if git fetch --quiet origin "$branch" 2>/dev/null; then git rebase --quiet "origin/$branch"; fi
git push --quiet --set-upstream origin HEAD
url="$(gh pr list --repo "$repo" --head "$branch" --state open --json url,headRepositoryOwner \
  --jq "map(select(.headRepositoryOwner.login == \"$owner\"))[0].url // empty")"
if [ -z "$url" ]; then
  body="${BODY:-$(git log --reverse --format='- %s' "$base/main..HEAD")}"
  url="$(gh pr create --repo "$repo" --base main --head "$owner:$branch" --title "$title" --body "$body" ${DRAFT:+--draft})"
else
  gh pr edit "$url" --title "$title" >/dev/null
fi
if [ -n "${DRAFT:-}" ]; then echo "✓ $url (draft, auto-merge off)"; exit 0; fi
if [ "$base" = upstream ]; then echo "✓ $url (from your fork; a maintainer reviews it and merges)"; exit 0; fi
gh pr merge "$url" --squash --auto >/dev/null
echo "✓ $url (squash auto-merge on)"
