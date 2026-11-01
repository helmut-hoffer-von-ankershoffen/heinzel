# Sonar security remediation checklist

Use this checklist when you work on the core's Sonar findings. Check current findings and dependency advisories again before you change anything.

## Establish the actual target

- A passing quality gate can coexist with a poor overall security rating: the gate may cover only new code.
- The security badge uses `security_rating`. A means zero unresolved vulnerabilities across the whole project.
- Review security hotspots separately. Reviewing a hotspot does not fix a vulnerability.
- Read the full source-to-sink flow before you group findings. Fix real vulnerabilities. Give each false positive a specific, reviewable explanation.
- Do not disable rules or accept risks to paint a badge.
- Check the PR analysis first. Then wait for the analysis of main and read the live badge.

## Git arguments need option boundaries

- An argv list prevents shell interpolation. It does not stop Git from reading a supplied revision as an option.
- Reject revisions with a leading hyphen. Pass `git rev-list --end-of-options <revision-range> --`.
- Resolve the Git executable with `shutil.which` and fail clearly if it is missing.
- Test against a temporary repository: an injected `--output` argument must not create a file, and a valid range must still work.

## Bound installation before disabling dependency builds

- Use frozen locks and `--no-build` for ordinary dependency installs.
- Keep reviewed local workspace builds and release builds explicit.
- A pinned Git tag still runs a source build. Prefer the official release wheel with a locked SHA-256.
- Prefer official binaries with per-platform SHA-256 values over tools that need a source build.
- Validate with a fresh cache and every required dependency group. A warm environment can hide a source-build path.
- `uv tool install` does not reliably check a URL hash fragment. Download and verify the bytes first, then install the local wheel with an explicit `--python`.

## Keep compatibility exceptions narrow

- Skip only the one package that has no wheel for a given Python version, and only in that matrix entry.
- Build it in a disposable environment from a SHA-256 pinned source archive and pinned backend wheels.
- Verify the source before the backend runs. Run that job without release credentials.
- Hash pinning proves identity, not the absence of known vulnerabilities. Keep dependency review enforced and audit every new backend pin.

## Distinguish intentional local interfaces from remote input

Review the full caller and trust boundary before you call a finding a false positive.

- File content that flows into the data argument of `write_text` does not control the destination path.
- A local tool wrapper may forward a developer's argv to a named executable without a shell. Check each caller.
- A Git hook may read a message file outside the checkout in a linked worktree.
- Release-note helpers may read local files. Check the fixed paths of the publishing caller.

These cases are not blanket exemptions. HTTP, MCP, plugin data, LLM-generated calls or higher privileges can add another input boundary.

## Badge and CI handoff

- Keep the status badges together directly below the README title.
- Link to real workflows and published artifacts. Label an unpublished package honestly.
- Do not show an aggregate package version or coverage number for a collection with independent versions.
- Skip coverage publication on fork and Dependabot PRs, but still measure it, and include that job in the aggregate CI gate.
- Keep pinned actions, minimal permissions and checkout with `persist-credentials: false`.

Reference: [Sonar metric definitions](https://docs.sonarsource.com/sonarqube-cloud/managing-your-projects/metric-definitions).
