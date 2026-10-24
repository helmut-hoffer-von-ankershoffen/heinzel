#!/usr/bin/env bash
# Open (or update) the pull request for this branch: preflight, push, PR to main, squash auto-merge.
# Usage: bash scripts/pr.sh "feat(scope): title"   BODY="..." sets the description; NO_PREFLIGHT=1 skips make preflight;
# DRAFT=1 opens a draft pull request and leaves auto-merge off, for proposals a maintainer decides on.
# Pull requests always target main: stacked pull requests merge into their base branch, not into main.
set -euo pipefail

title="${1:-}"
[ -n "$title" ] || { echo 'Usage: make pr TITLE="feat(scope): what changes"' >&2; exit 2; }
branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" != main ] || { echo "On main; create a branch first: git switch -c feat/short-name" >&2; exit 2; }
[ -z "$(git status --porcelain)" ] || { echo "Uncommitted changes; commit them first." >&2; exit 2; }

uv run --quiet cz check --message "$title" >/dev/null
uv run --quiet --script scripts/check_confidentiality.py --text "$title"
uv run --quiet --script scripts/check_confidentiality.py --branch
git fetch --quiet origin main
git merge-base --is-ancestor origin/main HEAD || { echo "Behind main; run make sync first." >&2; exit 2; }
[ -n "${NO_PREFLIGHT:-}" ] || make --no-print-directory preflight

# The update-prs workflow may have merged main into the remote branch; build on top of that instead of rejecting.
if git fetch --quiet origin "$branch" 2>/dev/null; then git rebase --quiet "origin/$branch"; fi
git push --quiet --set-upstream origin HEAD
if ! url="$(gh pr view --json url --jq .url 2>/dev/null)"; then
  body="${BODY:-$(git log --reverse --format='- %s' origin/main..HEAD)}"
  url="$(gh pr create --base main --title "$title" --body "$body" ${DRAFT:+--draft})"
else
  gh pr edit "$url" --title "$title" >/dev/null
fi
if [ -n "${DRAFT:-}" ]; then echo "✓ $url (draft, auto-merge off)"; exit 0; fi
gh pr merge "$url" --squash --auto >/dev/null
echo "✓ $url (squash auto-merge on)"
