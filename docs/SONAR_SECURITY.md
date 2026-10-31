# Sonar security remediation: lessons from the plugin repositories

Use this checklist when working on the core's Sonar findings. It records the investigation in
[Acme PR #13](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme/pull/13), not a claim that core is secure.
Verify current findings and dependency advisories again before applying a change.

## Establish the actual target

- A passing quality gate can coexist with an overall security C or E: the gate may cover only new code.
- The standard security badge uses `security_rating`. A means zero unresolved vulnerabilities, across the whole
  project. Inspect security hotspots separately; reviewing a hotspot does not fix a vulnerability.
- Read complete source-to-sink flows before grouping findings. Fix real vulnerabilities; record a specific,
  reviewable explanation for each legitimate false positive. Do not disable rules or accept risks to paint a badge.
- Check the PR analysis first, then wait for the post-merge main analysis and read the live badge. PR success alone
  does not verify main's rating. Sonar's compute queue can be empty while an automatic analysis is still starting.

## Git arguments need option boundaries

An argv list prevents shell interpolation but does not stop Git from treating a supplied revision as an option.
The confidentiality scanner's `--messages` argument originally reached `git rev-list` directly. Reject leading
hyphens and pass `git rev-list --end-of-options <revision-range> --`. Resolve the Git executable with
`shutil.which` and fail clearly if missing. Preserve ordinary commit ranges.

Regression tests should invoke the real scanner against a temporary repository: an injected `--output` argument
must not create a file, while a valid range must still find a confidential fixture without printing its content.
If Sonar retains a warning after this fix, document the current guards and separators rather than dismissing the
original finding as incorrect.

## Bound installation before disabling dependency builds

- Use frozen locks and `--no-build` for ordinary dependency installation, including direct helper invocations.
  Keep reviewed local workspace builds and deliberate release builds explicit.
- A pinned Git tag still executes a source build. Acme kept SDK version 0.7.2 and replaced its Git dependency with
  the official release wheel, locked its SHA-256, verified its GitHub attestation against the original tag commit,
  and compared its source files. Do not upgrade the SDK merely to clear the warning.
- `actionlint-py` required a source build. The existing native-tool installer can instead install the same
  actionlint version from official binaries, with per-platform SHA-256 values. Verify every supported platform.
- Validate using a fresh cache and all required dependency groups. A warm local environment can hide a missing
  wheel or an unintended source-build path.
- **Do not rely on a URL hash fragment with `uv tool install`.** A disposable test accepted an intentionally wrong
  `#sha256` fragment, although `uv pip install` rejected it. Download and verify the bytes before passing a local
  wheel to `uv tool install`; test that tampering stops before any subprocess. Select the intended interpreter
  explicitly with `--python`, since a runner's unrelated newer Python can lack dependency wheels.

## Keep compatibility exceptions narrow

PyYAML 6.0.3 had no Python 3.15 wheel. Acme's matrix skips only that package during its frozen wheel-only sync,
then performs an explicit pure-Python build in a disposable environment. Its source archive and backend wheels
are SHA-256 pinned; source verification happens before backend execution. The test job has no release credentials.
The resulting local wheel is installed without dependencies. Other matrix entries keep ordinary wheel-only sync.

The first backend pin, `wheel==0.45.1`, was caught by dependency review
([GHSA-8rrh-rw8j-w5fx](https://github.com/advisories/GHSA-8rrh-rw8j-w5fx)); the correction uses 0.46.3 with its official
PyPI wheel digest. Hash pinning proves identity, not absence of known vulnerabilities. Dependency review also caught setuptools 80.9.0
([GHSA-h35f-9h28-mq5c](https://github.com/advisories/GHSA-h35f-9h28-mq5c)); the backend now uses 84.2.1. Keep dependency
review enforced, audit new backend pins, and test the exact exceptional interpreter locally and in CI.

## Distinguish intentional local interfaces from remote input

Review the complete caller and trust boundary. Examples from Acme:

- `tools.pin` reads manifest content but writes to the fixed manifest path. A flow from file *content* to the
  data argument of `write_text` does not control the destination path.
- A local tool wrapper intentionally forwards a developer's argv to a named executable, without a shell or a
  remote/privileged caller. A general ban on arguments would break the interface; check each caller instead.
- A Git commit-message hook intentionally reads Git's supplied message filename, which may be outside a checkout
  in a linked worktree. Arbitrary local file selection is not automatically a traversal vulnerability.
- Release-note helpers deliberately read local files; review the actual publication caller's fixed paths.

These conclusions are not blanket exemptions for similar names in core. Check whether HTTP, MCP, plugin data,
LLM-generated calls or higher privileges introduce another input boundary. Each false-positive resolution needs
the exact current evidence and independent review.

## Badge and CI handoff

Keep the shared status badges together immediately below the README title. Link to real workflows and published
artifacts; label an unpublished package honestly. A collection has independent package versions, so it must not
show a fabricated aggregate PyPI version or coverage number. Coverage publication must skip fork and Dependabot
PRs, while measurement still runs. Include that measurement job in the aggregate CI gate. Preserve pinned actions,
minimal permissions, and checkout with `persist-credentials: false`.

References: [Sonar metric definitions](https://docs.sonarsource.com/sonarqube-cloud/managing-your-projects/metric-definitions),
[Acme's implementation and trust-boundary review](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme/blob/284a4b6afed536d5368ab332b07fcc5e5a508528/docs/SECURITY_REVIEW.md).
