#!/usr/bin/env bash
# Review a pull request's source with one command: bash scripts/review.sh <pr>   (make review PR=<n>)
# Reads the live pull request, decides the tier from its changed paths (scripts/review_tier.py), and for full or light
# dispatches workflow "Source review" on the protected ref with the exact head, base and trusted commit, then prints the
# run URL. Documentation-, test- and Markdown-only changes (tier none) cost nothing and dispatch nothing.
# Environment: REVIEW_TRUSTED (required) is the commit of the protected ref you have read; REVIEW_REF the ref's name
# (default trusted/source-review); REVIEW_REPO=owner/name overrides the repository taken from the git remotes; DRY_RUN=1 does every check and prints the dispatch instead of making it.
# Refuses a pull request that is closed, draft, merged, from a fork or not aimed at main, a base that is not current
# main, and a protected ref that is unprotected or not at REVIEW_TRUSTED. Exit 2: nothing was dispatched.
set -euo pipefail

die() { echo "error: $*" >&2; exit 2; }

pr="${1:-}"
[[ "$pr" =~ ^[1-9][0-9]{0,8}$ ]] || die 'usage: make review PR=<number>'
ref="${REVIEW_REF:-trusted/source-review}"
trusted="${REVIEW_TRUSTED:-}"
[[ "$trusted" =~ ^[a-f0-9]{40}$ ]] || die 'set REVIEW_TRUSTED to the full commit of the protected ref you have read (CONTRIBUTING.md, "Source review")'
here="$(cd "$(dirname "$0")/.." && pwd)"
[ -x "$here/.tools/bin/gh" ] && [ -z "${REVIEW_REPO:-}" ] && PATH="$here/.tools/bin:$PATH"  # the pinned gh, also when run without make
command -v gh >/dev/null || die 'needs gh; run make install'

# owner/name: the last two path parts of a remote URL, whatever the host or alias (https, ssh, a mirror).
slug() { git -C "$here" remote get-url "$1" | sed -E 's#/+$##; s#\.git$##; s#^.*[:/]([^/:]+/[^/:]+)$#\1#'; }
if [ -n "${REVIEW_REPO:-}" ]; then repo="$REVIEW_REPO"  # owner/name, for a mirror or a test
elif git -C "$here" remote | grep -qx upstream; then repo="$(slug upstream)"; else repo="$(slug origin)"; fi

live="$(gh api "repos/$repo/pulls/$pr" --jq '[.state,.draft,.merged,.base.ref,.head.sha,.base.sha,(.head.repo.full_name // ""),(.base.repo.full_name // ""),.changed_files]|@tsv')"
IFS=$'\t' read -r state draft merged base_ref head base head_repo base_repo changed <<<"$live"
[ "$state" = open ] || die "PR $pr is $state, not open"
[ "$merged" = false ] || die "PR $pr is merged"
[ "$draft" = false ] || die "PR $pr is a draft; mark it ready first"
[ "$base_ref" = main ] || die "PR $pr targets $base_ref, not main"
[ "$head_repo" = "$repo" ] && [ "$base_repo" = "$repo" ] || die "PR $pr comes from ${head_repo:-a deleted repository}, not $repo: forks are never reviewed this way"
main="$(gh api "repos/$repo/git/ref/heads/main" --jq .object.sha)"
[ "$main" = "$base" ] || die "PR $pr is based on ${base:0:12} but main is ${main:0:12}; update the branch and run again"

protected="$(gh api "repos/$repo/branches/$ref" --jq '[.commit.sha,.protected]|@tsv')" || die "protected ref $ref does not exist; a maintainer creates it first"
IFS=$'\t' read -r ref_sha ref_protected <<<"$protected"
[ "$ref_protected" = true ] || die "$ref has no branch protection; a maintainer protects it first"
[ "$ref_sha" = "$trusted" ] || die "$ref is at ${ref_sha:0:12}, not the REVIEW_TRUSTED ${trusted:0:12}; read the new commit, then update REVIEW_TRUSTED"

list="$(mktemp)"; trap 'rm -f "$list"' EXIT
gh api --paginate "repos/$repo/pulls/$pr/files" --jq '.[] | [.filename, (.previous_filename // empty)] | @json' >"$list"
[ "$(wc -l <"$list" | tr -d ' ')" = "$changed" ] || die "read $(wc -l <"$list" | tr -d ' ') of $changed changed files; the list is incomplete"
tier="$(cd "$here" && uv run --quiet --no-build --frozen --script scripts/review_tier.py --why --json <"$list")"
echo "PR $pr: tier $tier (head ${head:0:12}, base ${base:0:12})"
if [ "$tier" = none ]; then
  echo "Only documentation, tests and Markdown changed: no model run, nothing spent."
  exit 0
fi

args=(workflow run source-review.yml --repo "$repo" --ref "$ref" -f "pr=$pr" -f "target_head=$head" -f "target_base=$base" -f "trusted_commit=$trusted" -f "tier=$tier")
if [ "${DRY_RUN:-}" = 1 ]; then echo "dry run: gh ${args[*]}"; exit 0; fi
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
gh "${args[@]}"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 2
  url="$(gh run list --repo "$repo" --workflow source-review.yml --branch "$ref" --event workflow_dispatch --limit 5 --json url,createdAt --jq "map(select(.createdAt >= \"$since\")) | .[0].url // empty")"
  [ -z "$url" ] || { echo "$url"; exit 0; }
done
echo "dispatched; see the Actions tab"
