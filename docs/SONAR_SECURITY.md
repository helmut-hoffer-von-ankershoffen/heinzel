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
  `#sha256` fragment, although `uv pip install` rejected it. A later test found that `uv run --with` also accepted
  an intentionally wrong fragment. Download and verify the bytes before passing a local
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

## Applying the established fixes to core

Core's confidentiality scanner uses the same tested Git operand boundary. Its scope selector resolves a preflight
base to a verified commit SHA before passing it to Git or Make: argv alone does not prevent Make from expanding a
value such as `$(shell ...)`. Fast checks do not forward an unused base. Regression tests use real temporary
repositories and exercise both option-shaped input and Make expressions.

The released plugin toolkit remains version 0.2.1. `scripts/plugin_tooling.py` installs its complete three-package
runtime closure from `scripts/plugin-tooling-requirements.txt` with `--require-hashes --no-build`, in a disposable
environment. Core hooks, the scope selector, starter commands and protected publication use that launcher without
building or testing core for plugin-only work. Explicit local developer candidate wheels remain supported; remote
candidate URLs must carry a SHA-256 digest which the launcher checks before installation. Candidate dependencies
are wheel-only, but are not promised to match the released toolkit's lock.

CI's test dependency sync is frozen and wheel-only, with the same isolated, hash-verified Python 3.15 PyYAML build
described above. Stdlib-only publication checks select Python explicitly and load no project dependencies.
Native actionlint replaces the source-only wrapper at the same version, including generated governed templates.
The policy is scoped to these installation paths: core's all-group developer setup still permits the locked
watchdog source build required by macOS documentation tooling on Python 3.14. It does not have a global source-build
ban. Recheck the actual main metrics after merging; these changes alone do not resolve unrelated runtime findings.

### Core findings reviewed individually

Each row below was checked against core's own callers on 2026-10-31; the Acme conclusions were not carried over.
Sonar's trace is a taint-analysis claim, not evidence of a remote boundary. Resolve these in Sonar only with the
finding-specific text here, never with `NOSONAR` or an exclusion.

| Finding | Callers and boundary in core |
| --- | --- |
| `scripts/tools.py` `pin` (path traversal from file content) | `main` is the only caller and always passes the checked-in `scripts/tools.toml`; the tool name must already exist in it. The trace follows the manifest's *contents* into the data argument of `write_text`; the destination is the `manifest` parameter, never derived from contents. The written values are SHA-256 digests computed locally from downloads. A regression test shows that path-shaped manifest content cannot create or change another file. |
| `scripts/tools.py` `run` (argument injection) | Callers are `$(TOOLS) run <name>` in the Makefiles and the gitleaks hook in `.pre-commit-config.yaml`, with fixed tool names; the developer supplies that tool's arguments. The executable is the checksummed binary in `.tools` or, when no pinned build exists for the platform, the one on `PATH`. `os.execv` takes an argument vector, no shell. No HTTP, MCP, plugin or privileged caller reaches it, and `src/` never imports `scripts/`. |
| `scripts/check_confidentiality.py` `sources` (file read) | `--commit-msg` is the file Git hands the commit-msg hook (it can live in the shared Git directory of a linked worktree); positional files are the staged names `pre-commit` passes. The scanner only reads them and reports a term number and location, never content. It is not reachable from the application. |
| `scripts/release_notes.py` `main` (file read) | `publish.yml` and `release.sh` pass only a release tag and read the fixed `CHANGELOG.md`; the optional second argument is a local developer interface and no caller supplies it. The tag reaches `re.escape` only. |

Not reviewed in this pass, and so not resolved: runtime findings in `src/` (the install head, the launcher, the API
and web heads' URL handling, the approval hash), `docs/install.sh`, `desktop/ci`, `scripts/docs_record.py` and
`scripts/site_catalog.py`. Several of them cross a network or privilege boundary and need their own review.

References: [Sonar metric definitions](https://docs.sonarsource.com/sonarqube-cloud/managing-your-projects/metric-definitions),
[Acme's implementation and trust-boundary review](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme/blob/284a4b6afed536d5368ab332b07fcc5e5a508528/docs/SECURITY_REVIEW.md).
