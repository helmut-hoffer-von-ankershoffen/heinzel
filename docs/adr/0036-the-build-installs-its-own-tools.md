---
status: proposed
date: 2026-10-24
decision-makers: maintainer
related: 0007, 0011, 0013
---

# 0036: `make install` installs every tool the build needs, pinned and checksummed, on macOS and Linux

## Context and Problem Statement

Setting up a fresh Mac showed that `make setup` covered the Python tools but left the rest to be found and installed
by hand: `gh` for `make pr`, `make ci-logs` and `make release`; `vhs` with `ttyd` and `ffmpeg` for the README demo;
gitleaks, which CI downloaded as a pinned binary with a checksum while the pre-commit hook had prek build it from
source with a Go toolchain it downloaded first. pyright fetched an unpinned Node.js at its first run. External
contributors read in CONTRIBUTING.md that they need "uv and make" and then hit whatever their machine lacks. Which
programs does the build need, and how does a contributor get all of them, the same way on macOS and Linux and in CI?

Inventory, from the make targets, scripts, git hooks, tests and workflows:

| Program | Used by | Before | Now |
| --- | --- | --- | --- |
| git, make, bash | everything | prerequisite | prerequisite (macOS: Xcode command line tools) |
| uv | everything; installs Python | prerequisite | prerequisite (`make doctor` checks `required-version`) |
| ruff, mypy, pyright, pytest, prek, commitizen, copier, git-cliff, bump-my-version, pip-audit, import-linter, zizmor, typos, actionlint, shellcheck | lint, types, tests, hooks, release | dev dependency group | unchanged |
| Node.js (for pyright) | `make typecheck` | downloaded by pyright at its first run, unpinned | `pyright[nodejs]`: a wheel in uv.lock |
| gitleaks | pre-commit hook; CI history scan | hook: built from source by prek; CI: pinned binary | pinned binary, `build` group; hook and `make secrets` |
| gh | `make pr`, `make ci-logs`, `make release` | by hand | pinned binary, `build` group |
| vhs, ttyd, ffmpeg, jq | `make demo` | by hand | pinned binaries, `demo` group, on demand; ttyd from PATH on macOS |
| python3 | `scripts/template-test.sh` | the system's | uv's (`uv run --no-project python`) |
| curl, tar, unzip | CI only | runner | runner (`unzip` checks the wheel); nothing local needs them |
| claude | the Claude review workflow, agents | optional | optional, not part of the build |

## Decision Drivers

- A fresh clone needs only git, make and uv; nothing needs sudo or a system package manager.
- macOS (arm64, x86_64) and Linux (x86_64, arm64) behave the same, and CI runs what contributors run.
- Every downloaded program is pinned to a version and verified by checksum (supply chain, ADR 0011).
- `make install` stays idempotent and fast when everything is present, and prints one line per tool.
- Plugins get the same through the template (ADR 0007).

## Considered Options

- **Python tools stay in the dependency groups; other binaries are pinned per platform with a sha256 in
  `scripts/tools.toml` and installed by `scripts/tools.py` into `.tools/bin`** (chosen)
- **Homebrew (Brewfile) and apt**: differs per OS and distribution, needs sudo on Linux, versions float with the
  package manager, and CI would install differently from contributors.
- **mise, aqua or asdf**: pins and verifies binaries well, but is one more program to install before `make setup`, and
  its plugin registries are another supply chain to trust; the set here is five small binaries.
- **PyPI wrappers for everything**: no maintained wheels for gh, gitleaks, vhs or ttyd; where one exists for a binary
  (imageio-ffmpeg), its file is pinned by checksum without making it a project dependency.
- **prek-managed hooks for gitleaks (Go toolchain)**: works without a system Go, but downloads a toolchain and
  builds on the first commit, and CI's history scan would still need its own binary: two pins for one tool.
- **Containers (dev container)**: reproducible, but heavy for a Python project, and hooks and `hz` would run inside a
  container instead of on the contributor's machine.

## Decision Outcome

Chosen option: **dependency groups for Python tools, and pinned, checksummed binaries in `.tools/bin` for the rest**,
because it needs nothing beyond git, make and uv, works identically on all four platforms, and gives each tool exactly
one pin that hooks, make and CI share.

- `scripts/tools.toml` lists each binary: version, group (`build` or `demo`), why it is needed, and per platform the
  file and its sha256. `scripts/tools.py` (standard library only, so it runs before `uv sync`) downloads a file,
  refuses it unless the checksum matches, extracts the binary and links it from `.tools/bin/<name>`; downloads are
  cached in `~/.cache/heinzel-dev/downloads` by checksum, so more clones and worktrees cost nothing.
- `make install` runs `uv sync --all-groups` and installs the `build` group; `make setup` adds the git hooks. Both
  are idempotent; with everything present they take about a second. Every make target has `.tools/bin` first on PATH.
- Optional tools install when their target needs them (`demo` group for `make demo`). A platform without a pinned
  file (ttyd on macOS) uses the program on PATH and says how to install it (`brew install ttyd`); the same fallback,
  with a warning, covers a pinned download that fails.
- The gitleaks pre-commit hook and `make secrets` (CI's history scan) run the pinned binary through
  `tools.py run gitleaks`. pyright takes Node.js from the `nodejs-wheel-binaries` wheel (`pyright[nodejs]`).
- `make doctor` lists the prerequisites, the environment, every tool and the gh login, each missing one with the
  command that fixes it, and fails while something required is missing.
- CI's `make setup` job runs `make setup` and `make doctor` on ubuntu-latest, ubuntu-24.04-arm, macos-latest and
  macos-15-intel, and `make tools-verify` downloads every pinned file of every platform and checks it.
- Bumping a tool: change its version in `tools.toml` and run `uv run --script scripts/tools.py pin <name>`.

### Consequences

- Good: contributors on a fresh machine run `make setup` and are done; CI and the hooks run the same binaries;
  plugins get it through the template.
- Good: one place lists every program the build needs and why.
- Bad: Dependabot doesn't update `tools.toml`; the pins are bumped by hand (a `pin` command makes it one step). The
  gitleaks hook no longer follows the pre-commit `rev` that Dependabot could update.
- Bad: vhs still downloads its own Chromium at the first recording, unpinned; acceptable for an optional target.
- Bad: about 60 MB of binaries per machine for the build group, once.

### Confirmation

`tests/unit/tools_test.py` checks the manifest (every `build` tool pinned on all four platforms, checksums, https),
installing, checksum refusal, caching, upgrades, the PATH fallback, `run`, `pin` and `make doctor`, that the Makefile
installs the tools and puts `.tools/bin` first, and that gitleaks has no second pin in the hooks or workflows. CI's
`make setup` job proves the bootstrap on each platform and `make tools-verify` the pins.

## More Information

[CONTRIBUTING.md](../../CONTRIBUTING.md) lists the prerequisites; `make doctor` explains what is missing.
