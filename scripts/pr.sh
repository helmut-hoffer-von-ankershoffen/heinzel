#!/usr/bin/env bash
# Open (or update) the pull request for this branch: preflight, push, PR to main for review.
# Usage: bash scripts/pr.sh "feat(scope): title"   BODY="..." or BODY_FILE=path sets the description of a new pull
# request (default: the commit subjects); NO_PREFLIGHT=1 skips make preflight;
# DRAFT=1 opens a draft pull request for proposals a maintainer decides on.
# Pull requests always target main: stacked pull requests merge into their base branch, not into main.
# From a fork (a remote named `upstream` next to your `origin`), the branch goes to your fork and the pull request
# to upstream's main. A maintainer reviews checks and merges after review.
# Exit 2: nothing was pushed, and the message says what to do.
set -euo pipefail

title="${1:-}"
[ -n "$title" ] || { echo 'Usage: make pr TITLE="feat(scope): what changes"' >&2; exit 2; }
branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" != main ] || { echo "On main; create a branch first: git switch -c feat/short-name" >&2; exit 2; }
[ -z "$(git status --porcelain)" ] || { echo "Uncommitted changes; commit them first." >&2; exit 2; }
if [ -n "${BODY_FILE:-}" ]; then
  [ -z "${BODY:-}" ] || { echo "Set BODY or BODY_FILE, not both." >&2; exit 2; }
  [ -f "$BODY_FILE" ] && [ -r "$BODY_FILE" ] || { echo "BODY_FILE=$BODY_FILE: no such readable file." >&2; exit 2; }
  [ -n "$(tr -d '[:space:]' < "$BODY_FILE")" ] || { echo "BODY_FILE=$BODY_FILE is empty." >&2; exit 2; }
fi
PATH="$(cd "$(dirname "$0")/.." && pwd)/.tools/bin:$PATH"  # the pinned gh, also when run without make
command -v gh >/dev/null || { echo "make pr needs gh; run make install (make doctor shows what is missing)." >&2; exit 2; }

# owner/name: the last two path parts of a remote URL, whatever the host or alias (https, ssh, a mirror).
slug() { git remote get-url "$1" | sed -E 's#/+$##; s#\.git$##; s#^.*[:/]([^/:]+/[^/:]+)$#\1#'; }
base=origin
origin_slug="$(slug origin)"
if ! origin_repo="$(gh repo view "$origin_slug" --json nameWithOwner --jq '.nameWithOwner // empty')"; then
  echo "Cannot verify the origin repository; nothing was pushed." >&2
  exit 2
fi
if ! [[ "$origin_repo" =~ ^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "Cannot safely identify the origin repository; nothing was pushed." >&2
  exit 2
fi
owner="${origin_repo%%/*}"
repo="$origin_repo"
if git remote | grep -qx upstream; then
  base=upstream
  repo="$(slug upstream)"
fi

uv run --quiet cz check --message "$title" >/dev/null
uv run --quiet --script scripts/check_confidentiality.py --text "$title"
uv run --quiet --script scripts/check_confidentiality.py --branch
git fetch --quiet "$base" main
# Being behind main is fine: main's own CI gates a release (ADR 0114). Only a real conflict has to be resolved first.
git merge-tree --write-tree HEAD "$base/main" >/dev/null 2>&1 || { echo "Conflicts with $base/main; run make sync first." >&2; exit 2; }

# The update-prs workflow may have merged main into the remote branch; the push below builds on top of that instead
# of being rejected. But a branch rebased onto main here (or squashed, amended, reordered) diverges from its remote
# branch, and rebasing it onto that would replay main's commits onto the old branch: stop and let a person decide.
remote_branch=""
if git fetch --quiet origin "$branch" 2>/dev/null; then
  remote_branch="origin/$branch"
  if ! git merge-base --is-ancestor "$remote_branch" HEAD &&
    [ -n "$(git rev-list -n 1 --no-merges "$base/main" "^$remote_branch")" ]; then
    remote_sha="$(git rev-parse "$remote_branch")"
    # What a force-push would drop: the remote's commits with no equivalent in this branch (main's are all in it).
    dropped="$(git log --format='      %h %s' --no-merges --cherry-pick --right-only "HEAD...$remote_branch")"
    {
      echo "$branch and $remote_branch have diverged: $branch was rebased onto main here (or rewritten), and"
      echo "rebasing it onto $remote_branch would replay main's commits onto the old branch, so make pr stops."
      if [ -z "$dropped" ]; then
        echo "Every commit of $remote_branch is in $branch as an equivalent commit or merges main."
      else
        echo "These commits of $remote_branch have no equivalent in $branch; a force-push drops them, so"
        echo "cherry-pick the ones to keep first (git cherry-pick <sha>):"
        echo "$dropped"
      fi
      echo "Then either"
      echo "  - replace the remote branch with this one (refused if someone pushes to it after this check):"
      echo "      git push --force-with-lease=$branch:$remote_sha origin $branch"
      echo "    and run make pr again, or"
      echo "  - push this branch under a new name, which opens a new pull request (close the old one):"
      echo "      git switch -c $branch-2 && make pr TITLE=\"...\""
    } >&2
    exit 2
  fi
fi
# Preserve remote and local merge history; preflight the resulting commit before pushing.
if [ -n "$remote_branch" ] && ! git merge-base --is-ancestor "$remote_branch" HEAD; then
  git merge --quiet --no-edit "$remote_branch"
fi
tested_head=""
if [ -z "${NO_PREFLIGHT:-}" ]; then
  tested_head="$(git rev-parse HEAD)"
  if [ -f scripts/scoped_checks.py ]; then
    uv run --no-project --script scripts/scoped_checks.py preflight --base "$base/main"
  else
    make --no-print-directory preflight BASE="$base/main"
  fi
fi

if [ -n "${BODY_FILE:-}" ]; then body="$(cat "$BODY_FILE")"; else body="${BODY:-}"; fi
[ -n "$body" ] || body="$(git log --reverse --format='- %s' "$base/main..HEAD")"
uv run --quiet --script scripts/check_confidentiality.py --text "$body"

# Existing automatic merges must be turned off by a maintainer before updating the PR.
url="$(gh pr list --repo "$repo" --base main --head "$branch" --state open --limit 101 --json url,headRepository \
  --jq "if length > 100 then error(\"Too many matching pull requests\") elif any(.[]; (.headRepository.nameWithOwner | if type == \"string\" then (length > 201 or (test(\"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$\") | not)) else true end)) then error(\"Invalid head repository\") else map(select((.headRepository.nameWithOwner | ascii_downcase) == \"$(printf "%s" "$origin_repo" | tr "[:upper:]" "[:lower:]")\")) | if length == 0 then \"\" elif length == 1 then .[0].url | if type == \"string\" and length > 0 then . else error(\"Missing pull request URL\") end else error(\"Ambiguous pull requests\") end end")"
if [[ -n "$url" ]]; then
  if ! auto_merge="$(gh pr view "$url" --json autoMergeRequest --jq 'if has("autoMergeRequest") then if .autoMergeRequest == null then "disabled" else "enabled" end else "unknown" end')"; then
    echo "Cannot verify the pull request's auto-merge state; nothing was pushed." >&2
    exit 2
  fi
  if [[ "$auto_merge" != disabled ]]; then
    echo "Auto-merge is enabled or unknown; disable it before updating this pull request." >&2
    exit 2
  fi
fi
if [[ -n "$tested_head" ]] && { [[ "$(git rev-parse HEAD)" != "$tested_head" ]] || [[ -n "$(git status --porcelain)" ]]; }; then
  echo "The working tree changed after preflight; review the changes and run make pr again." >&2
  exit 2
fi
git push --quiet --set-upstream origin HEAD
if [ -z "$url" ]; then
  url="$(gh pr create --repo "$repo" --base main --head "$owner:$branch" --title "$title" --body "$body" ${DRAFT:+--draft})"
else
  gh pr edit "$url" --title "$title" >/dev/null
  if [ -n "${BODY:-}${BODY_FILE:-}" ]; then
    echo "The pull request exists, so its description stays; change it with: gh pr edit --body-file <path>" >&2
  fi
fi
if [ -n "${DRAFT:-}" ]; then echo "✓ $url (draft, auto-merge off)"; exit 0; fi
echo "✓ $url (review and checks required before merging)"
