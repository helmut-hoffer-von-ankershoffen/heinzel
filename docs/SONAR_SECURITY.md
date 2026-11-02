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

## Applying the established fixes to core

Core's confidentiality scanner uses the same tested Git operand boundary. Its scope selector resolves a preflight
base to a verified commit SHA before passing it to Git or Make: argv alone does not prevent Make from expanding a
value such as `$(shell ...)`. Fast checks do not forward an unused base.

Both scripts rebuild a revision from a ref name that Git lists, hex digits or a `HEAD` offset, and refuse anything
else. `plugin_tooling.py` runs only an `hz-plugin` subcommand or a `heinzel_plugin_tools` module. Regression tests use
real temporary repositories and exercise both option-shaped input and Make expressions.

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

Each row below was checked against core's own callers against the current code; the ACME conclusions were not carried over.
Sonar's trace is a taint-analysis claim, not evidence of a remote boundary. Resolve these in Sonar only with the
finding-specific text here, never with `NOSONAR` or an exclusion.

| Finding | Callers and boundary in core |
| --- | --- |
| `scripts/tools.py` `pin` (path traversal from file content) | `main` is the only caller and always passes the checked-in `scripts/tools.toml`; the tool name must already exist in it. The trace follows the manifest's *contents* into the data argument of `write_text`; the destination is the `manifest` parameter, never derived from contents. The written values are SHA-256 digests computed locally from downloads. A regression test shows that path-shaped manifest content cannot create or change another file. |
| `scripts/tools.py` `run` (argument injection) | Callers are `$(TOOLS) run <name>` in the Makefiles and the gitleaks hook in `.pre-commit-config.yaml`, with fixed tool names; the developer supplies that tool's arguments. The executable is the checksummed binary in `.tools` or, when no pinned build exists for the platform, the one on `PATH`. `os.execv` takes an argument vector, no shell. No HTTP, MCP, plugin or privileged caller reaches it, and `src/` never imports `scripts/`. |
| `scripts/check_confidentiality.py` `sources` (file read) | `--commit-msg` is the file Git hands the commit-msg hook (it can live in the shared Git directory of a linked worktree); positional files are the staged names `pre-commit` passes. The scanner only reads them and reports a term number and location, never content. It is not reachable from the application. |
| `scripts/release_notes.py` `main` (file read) | `publish.yml` and `release.sh` pass only a release tag and read the fixed `CHANGELOG.md`; the optional second argument is a local developer interface and no caller supplies it. The tag reaches `re.escape` only. |

Runtime and remaining findings, reviewed by reading each caller. Nothing here was exercised against a
hostile client, and none of it is a full audit of the heads.

| Finding | Evidence |
| --- | --- |
| API and web heads `S5332` (`http://` strings) | The API builds strings that are compared with the `Origin` header after the Host allowlist; nothing connects to them. Configured proxy origins are `https://` only. The web head announces a loopback URL for a socket bound to `127.0.0.1`, with the token in the fragment. |
| `kernel/_approval.py` `S2053` (no salt) | All seven approval issuers create the proof with `secrets.token_urlsafe(32)`: 256 random bits. The digest only locates a record in the owner journal. A salt protects low-entropy secrets, which these are not. |
| `web/src/sound/StreamPlayer.tsx` `S2819` | The handler calls `playerEvent`, which returns nothing unless `message.origin` equals the server-provided origin and `message.source` is the frame's own window. `stream.test.ts` covers both checks. |
| `src/_heinzel_launcher.py` `S8705` | A console-script entry that nothing imports. The module name is a literal, `-I -m` ends interpreter option parsing, and the caller's arguments go to that module. |
| `heads/install.py` `S8703`, `S8705`, `S8707` | Only the `hz-install` console script and `heads.bootstrap` (which supplies its own receipt) reach it; no REST, MCP or web path does. The caller chooses the profile and so what to install; wheel paths are digest-verified, and a remote profile cannot select a local file. |
| `scripts/docs_record.py` `S5332` | A regular expression that matches the loopback URL a child `hz ui` prints. It opens no connection. |
| `scripts/site_catalog.py` `S8707` | `--output` comes from `make docs-catalog` (an environment variable or the fixed `docs/_data/catalog.json`), never from the fetched catalog data, which is only file content. |
| `scripts/starter-test.py` `S8705` | Its one caller, `template-test.sh`, passes the wheel it built; the value is an option value or a Make variable that the starter Makefile takes literally with `$(value ...)`, and no shell is involved. |
| `desktop/ci/restore_linux_sandbox.py` `S2612` | Mode 4755 is deliberate: Chromium's setuid sandbox helper requires it. The bytes are SHA-256 pinned before the change, the descriptor is single-link and root-owned, and the mode is re-read afterwards. It is world-executable setuid root, so this is an accepted risk, not a false positive. |

Workflow steps that run stdlib-only scripts use `uv run --no-project --frozen --no-build --python 3.14 python ...`.
uv warns that `--frozen` has no effect together with `--no-project`: these runs have no project lock, and the flag
asserts no guarantee. It is there because Sonar's S8544 only accepts a frozen invocation; the isolation comes from
`--no-project --no-build` and from the scripts having no dependencies.

References: [Sonar metric definitions](https://docs.sonarsource.com/sonarqube-cloud/managing-your-projects/metric-definitions),
[ACME's implementation and trust-boundary review](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme/blob/284a4b6afed536d5368ab332b07fcc5e5a508528/docs/SECURITY_REVIEW.md).
