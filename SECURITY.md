# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately, not through public issues:
[open a private security advisory](../../security/advisories/new) for this repository (its Security tab →
**Report a vulnerability**; GitHub explains the steps in
[Privately reporting a security vulnerability](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately)).
Expect an acknowledgement within a few working days. Include what is affected, how to reproduce it and what an
attacker gains.

## Supported versions

Only the latest release receives security fixes while the project is on 0.x.

## What Heinzelmännchen does on your machine

- It runs with your user's rights and writes only to its configuration, data and cache directories (see
  `hz system config show`) and to the uv tool environment it is installed in.
- **Plugins run with the same rights.** Only add plugins you trust, from sources you trust; `hz system plugin list`
  shows where each one came from.
- `hz` sends nothing anywhere on its own: no telemetry. Network access happens when you install, update or add
  plugins (through uv), when you run `hz system release` (one GET to api.github.com with only a `heinzel/<version>`
  User-Agent, no token; [ADR 0031](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/adr/0031-update-check.md)) or `hz system plugin search` (the same, plus your
  GitHub token if you configured one), and when a plugin you installed calls a service. `hz --version` and
  `hz system info` only read what `hz system release` cached.
- `hz system serve mcp|api` listens on `127.0.0.1` unless you pass another `--host`; the servers have no
  authentication, so don't expose them to a network you don't trust.
- External commands (uv, git, copier) run without a shell, from argument lists, with timeouts.
- **The daemon runs jobs unattended.** Once you run `hz system daemon install`, launchd or systemd start
  `hz system daemon tick` every few minutes, at login too, and it runs the jobs your modules and plugins declare,
  with your rights, without asking. Nothing is installed unless you run that command on the command line (it is
  not an MCP tool or REST endpoint). `hz system daemon jobs` lists what would run; `hz system daemon pause` stops
  all jobs until `resume`, `hz system daemon uninstall` removes the launchd agent or systemd timer, and
  `disabled_modules` switches off a module's jobs. Receipts, state and logs are readable only by you (0700/0600).
- Credentials that modules and plugins need are kept in your OS keychain (`hz system credential set NAME` reads
  the value from a hidden prompt or standard input) and are never shown, logged or returned; on Linux without a
  keychain they are read from `HZ_CREDENTIAL_<SERVICE>_<NAME>` variables, and `hz system doctor` warns about it.
  Credentials are not isolated between plugins: every plugin runs in the same process with your rights and can read
  all of them, which is one more reason to add only plugins you trust.

## How we keep the project secure

| Area | Measure | Where |
| --- | --- | --- |
| Dependencies | Locked in `uv.lock`; CI installs exactly the lockfile | `UV_FROZEN` in CI |
| | Known vulnerabilities in runtime dependencies fail the build (pip-audit, daily as well) | `make audit`, CI |
| | Licenses of runtime dependencies must be on an allow-list (permissive, plus LGPL/MPL), so releases stay MIT | `.license-allowlist`, `make licenses` (in `make audit` and Publish) |
| | New dependencies with moderate or worse advisories block pull requests | dependency-review in CI |
| | Weekly grouped updates with a 7-day cooldown, so compromised releases are usually caught first; security updates as they appear | Dependabot (`.github/dependabot.yml`), Dependabot alerts and security updates |
| Code | Static analysis on every pull request and weekly | CodeQL |
| | Strict typing and an extensive lint rule set, including security rules (`S`) | ruff, mypy, pyright |
| Secrets | Credentials (API keys, tokens, private keys) are blocked on commit and the whole history is scanned in CI | gitleaks (prek hook, `Secret scan` job) |
| | Pushes with known secret formats are rejected | GitHub secret scanning with push protection |
| | Credentials modules need live in the OS keychain behind one injected service, never in plaintext files; values never appear in output, logs or results, and the environment fallback is a doctor warning (ADR 0030) | `heinzel.Credentials`, `hz system credential`, `tests/integration/credential_test.py` |
| | Confidential names (employers, customers, internal projects) are blocked in files, commit messages, branch names and pull request titles, against a private list kept outside the repository | `scripts/check_confidentiality.py`, hooks, CI, Publish |
| Background jobs | Installed only from the CLI; jobs run with your rights, each with a timeout, and are listed, paused and removed with `hz system daemon jobs`, `pause` and `uninstall`; receipts, state and logs are private files written atomically; the configuration a project's `hz.toml` sets can't move them (ADR 0020) | `hz system daemon`, `tests/unit/modules/daemon_service_test.py` |
| Workflows | Actions pinned to full commit SHAs, read-only token by default, least privilege per job, no persisted credentials, no untrusted input in `run:` | zizmor, actionlint (`make lint`) |
| | Supply-chain posture tracked | OpenSSF Scorecard |
| Changes | `main` only changes through squash-merged pull requests with green CI (`CI OK`), linear history, no force pushes or deletions | repository ruleset |
| | Reviewer agents, including a security reviewer, on pull requests (opt-in) | `.claude/agents/`, `claude-review.yml` |
| Releases | Tags are created by the release workflow for release commits only; Publish refuses a tag that doesn't match the version | `release-tag.yml`, `publish.yml` |
| | Wheels, source distributions and the SBOM carry Sigstore build provenance attestations | `actions/attest-build-provenance` |
| | Each release ships a CycloneDX software bill of materials of its runtime dependencies | `*.cdx.json` release asset |

Verify a release artifact:

```bash
gh attestation verify heinzel-0.1.0-py3-none-any.whl --repo helmut-hoffer-von-ankershoffen/heinzel
```

Plugins created from the template inherit the same hooks, checks and release workflow.
