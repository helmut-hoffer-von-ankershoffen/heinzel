# Security policy

<!-- badges:start -->
[![CI](https://img.shields.io/github/actions/workflow/status/helmut-hoffer-von-ankershoffen/heinzel/ci.yml?branch=main&label=CI)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/python-coverage-comment-action-data/endpoint.json)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/tree/python-coverage-comment-action-data)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/helmut-hoffer-von-ankershoffen/heinzel/codeql.yml?branch=main&label=CodeQL)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://img.shields.io/ossf-scorecard/github.com/helmut-hoffer-von-ankershoffen/heinzel?label=OpenSSF%20Scorecard)](https://scorecard.dev/viewer/?uri=github.com/helmut-hoffer-von-ankershoffen/heinzel)
[![OpenSSF Best Practices](https://img.shields.io/cii/level/15863?label=OpenSSF%20Best%20Practices)](https://www.bestpractices.dev/en/projects/15863/passing)
[![PyPI](https://img.shields.io/pypi/v/heinzel)](https://pypi.org/project/heinzel/)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue)](pyproject.toml)
[![Quality Gate Status](https://img.shields.io/sonar/quality_gate/helmut-hoffer-von-ankershoffen_heinzel?server=https://sonarcloud.io&label=Quality%20Gate%20Status)](https://sonarcloud.io/summary/overall?id=helmut-hoffer-von-ankershoffen_heinzel)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=helmut-hoffer-von-ankershoffen_heinzel&metric=security_rating)](https://sonarcloud.io/summary/overall?id=helmut-hoffer-von-ankershoffen_heinzel)
[![License: MIT](https://img.shields.io/badge/license-MIT-44bb00)](LICENSE)
<!-- badges:end -->

## Reporting a vulnerability

Please report vulnerabilities privately, not through public issues:
[open a private security advisory](../../security/advisories/new) for this repository (its Security tab →
**Report a vulnerability**; GitHub explains the steps in
[Privately reporting a security vulnerability](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately)).
Include what is affected (version, command, plugin), how to reproduce it and what an attacker gains. If you can't use
GitHub, open a public issue that asks for a private contact, without any details of the vulnerability.

## How we handle a report

| Step | What happens | When |
| --- | --- | --- |
| Acknowledge | A maintainer replies in the advisory | Within 7 days of the report |
| Assess | We confirm or reject the report, rate its severity with CVSS and tell you | Within 14 days of the report |
| Fix | The fix is developed in the advisory's private fork, reviewed and tested like every change, and released | Critical and high: as fast as possible, at most 30 days; medium: within 60 days; low: in a regular release |
| Disclose | The advisory is published with a CVE (GitHub assigns it), the affected and fixed versions; the release notes and `CHANGELOG.md` list the fix under **Security** and name the advisory | With the release that fixes it |
| Credit | You are credited in the advisory and the release notes, unless you ask not to be | With the disclosure |

We keep you informed at least every 14 days until the advisory is published, and ask you not to disclose the
vulnerability before the fix is released or 90 days have passed, whichever comes first. Vulnerabilities in a
dependency are fixed by updating it (Dependabot security updates, `make audit`) in a release, with the same
deadlines: Dependabot security updates and weekly updates cover the Python packages, the web app's packages and the
GitHub Actions, and `make audit` (pip-audit and `pnpm audit`) fails the build while a known vulnerability remains. Such
a fix is committed as `fix(security): ...` or names the advisory (GHSA or CVE) in its body, so the release notes list
it under **Security** too. A vulnerability in a plugin from another repository is reported to that plugin's
repository.

## Supported versions

Only the latest release receives security fixes while the project is on 0.x; update with `hz system update`.

## What Heinzelmännchen does on your machine

- It runs with your user's rights and writes only to its configuration, data and cache directories (see
  `hz system config show`) and to the uv tool environment it is installed in.
- **Plugins run with the same rights.** Only add plugins you trust, from sources you trust; `hz system plugin list`
  shows where each one came from.
- `hz` sends nothing about you anywhere: no telemetry, crash reports or usage pings. Its one request of its own is
  the update check: at most once an hour, on a terminal and outside CI, one GET to api.github.com for the latest
  release, with only a `heinzel/<version>` User-Agent, no token and no identifier, in the background with a 2-second
  timeout; `[hz] update_check = false` or `HZ_UPDATE_CHECK=false` turns it off
  ([ADR 0031](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/adr/0031-update-check.md)).
  Other network access happens when you install, update or add plugins (through uv), when you run
  `hz system release` (the same request) or `hz system plugin search` (the same kind, plus your GitHub token if
  you configured one), and when a plugin you installed calls a service.
- **Sound in the web app is off until you press Play.** A stream soundscape (Claude FM or a plugin's)
  plays in its host's own player; the default is YouTube's privacy-enhanced `youtube-nocookie.com`, named
  in code, never by a plugin or a configuration file. Nothing of it loads before you press Play, and the page's CSP
  admits YouTube's two fixed player origins only while a stream is the soundscape in effect. “Use YouTube sign-in”
  switches to the regular `youtube.com` player and remembers this browser's choice. It can use your YouTube account
  when the browser allows it; “Use private player” reverses the choice
  ([ADR 0092](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/adr/0092-youtube-sign-in-is-an-explicit-player-choice.md)).
  Once you press Play, YouTube receives
  your IP address and the app's local address (`http://127.0.0.1:<port>`), and may keep storage in its frame. A
  repository's `hz.toml` can't switch sound on
  ([ADR 0058](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/adr/0058-soundscapes-are-data-a-plugin-ships-chosen-like-a-theme.md)).
- `hz system serve mcp|api` listens on `127.0.0.1` unless you pass another `--host`; the servers have no
  authentication, so don't expose them to a network you don't trust. The REST server answers only requests to
  `localhost`, `127.0.0.1` or `[::1]` on its port (or names you list in `[hz] api_allowed_hosts`) and refuses
  requests another site made, so a web page can't call it; `hz heinzel wake` and `hz system daemon tick` aren't
  REST endpoints at all.
- External commands (uv, git, copier, and vendor CLIs such as `gh` that adapters call) run without a shell, from
  argument lists, with timeouts. An adapter that calls a vendor CLI acts with that CLI's login, which may allow
  more than the adapter needs; the GitHub plugin only sends GET requests.
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
| | Commit emails made up from the machine's hostname (`name@Some-MacBook-Air.local`) are refused, so commits publish a mailbox or GitHub's noreply address, not a machine name | `check_confidentiality.py --identity` (pre-commit hook) |
| Background jobs | Installed only from the CLI; jobs run with your rights, each with a timeout, and are listed, paused and removed with `hz system daemon jobs`, `pause` and `uninstall`; receipts, state and logs are private files written atomically; the configuration a project's `hz.toml` sets can't move them (ADR 0020) | `hz system daemon`, `tests/unit/modules/daemon_service_test.py` |
| Local servers | The REST head refuses requests whose Host isn't a loopback name on its port (DNS rebinding) and requests from another origin, with `403`; operations that spend money or act unattended aren't REST endpoints (ADR 0019) | `heinzel.heads.api`, `tests/integration/rest_security_test.py` |
| Terminal output | The CLI prints values from configuration, plugins and contracts as plain text without control characters, so escape sequences (colours, OSC 8 links, OSC 52 clipboard writes, C1 controls) never reach your terminal; JSON escapes them | `heinzel.heads.cli._render`, `tests/integration/cli_plain_text_test.py` |
| Workflow contracts | Contract files are data: the parser refuses aliases, tags, deep nesting, control characters and large files; one bad file is only that file's diagnostic; contracts read only what they declare, through adapters configuration binds, with a user-scope cost cap; drafts are published by nobody, and they and their records (the fetched inputs) are deleted after `[heinzel] retention_days` (90 by default) without following symbolic links, while receipts keep only their hashes (ADR 0021) | `hz heinzel check`, `hz heinzel prune`, `tests/unit/modules/heinzel_contracts_test.py`, `tests/unit/modules/heinzel_service_test.py`, `tests/unit/modules/heinzel_retention_test.py` |
| | The parser is fuzzed with atheris (weekly and when it changes) and checked with hypothesis properties in every test run: whatever the input, it reports diagnostics and never needs its isolation boundary | `fuzz/contract_fuzz.py` (`fuzz.yml`), `tests/unit/modules/heinzel_contracts_property_test.py` |
| Workflows | Actions pinned to full commit SHAs, read-only token by default, least privilege per job, no persisted credentials, no untrusted input in `run:` | zizmor, actionlint (`make lint`) |
| | Supply-chain posture tracked | OpenSSF Scorecard |
| Changes | `main` only changes through squash-merged pull requests with green CI (`CI OK`), linear history, no force pushes or deletions | repository ruleset |
| | Reviewer agents, including a security reviewer, on pull requests (opt-in) | `.claude/agents/`, `claude-review.yml` |
| Releases | Tags are created by the release workflow for release commits only; Publish refuses a tag that doesn't match the version | `release-tag.yml`, `publish.yml` |
| | Wheels, source distributions and the SBOM carry Sigstore build provenance attestations, attached to each release as the Sigstore bundle (`provenance.sigstore.json`) and its signed in-toto SLSA provenance (`provenance.intoto.jsonl`) | `actions/attest-build-provenance`, `publish.yml` |
| | Each release ships a CycloneDX software bill of materials of its runtime dependencies | `*.cdx.json` release asset |

Verify a release artifact:

```bash
gh attestation verify heinzel-<version>-py3-none-any.whl --repo helmut-hoffer-von-ankershoffen/heinzel
```

Plugins created from the template inherit the same hooks, checks and release workflow.

## Cryptography

Heinzelmännchen implements no cryptography of its own and calls only Python's standard library for it: `secrets` for
every token and random identifier (256-bit session and device tokens for the local web app), `hashlib` SHA-256 for
content digests and for storing device tokens (only their hash is kept, ADR 0046) and `hmac.compare_digest` to
compare tokens in constant time. It stores no user passwords; credentials live in the OS keychain. Network access
uses HTTPS through the standard TLS stack with certificate verification (Python's `ssl` defaults through `urllib`,
and uv, git and the vendor CLIs), and the local servers listen on loopback (`127.0.0.1`) by default, without TLS.

When you explicitly configure a personal state repository, hz pushes day-granular presence, version and OS-family metadata with your roster handle and a random machine id in its writer branch. Commit identity uses the handle and an `hz.invalid` address; dates are day-only. Nothing is pushed by default; team upload requires separate consent in a later slice.
