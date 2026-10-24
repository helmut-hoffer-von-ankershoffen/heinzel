---
status: accepted
date: 2026-10-24
decision-makers: maintainer
related: 0005, 0012, 0015, 0018, 0020, 0027, 0028
---

# 0030: Credentials live in the OS keychain behind a credentials service

## Context and Problem Statement

Plugins and the planned capability adapters ([ADR 0028](0028-integrations-are-capability-adapters.md)) need API tokens
and similar secrets, and the daemon ([ADR 0020](0020-daemon-head.md) §10) already says secrets come from the OS
keychain through `keyring`, never from unit files. Without one way to store and read them, each plugin invents its own:
a token file here, an environment variable there, a value in `config.toml` that ends up in a team repository. How do
modules and plugins get secrets, where are they kept, and how does a person see what is missing without seeing values?

## Decision Drivers

- Secrets never sit in plaintext files Heinzelmännchen writes, and never appear in output, logs or results that an
  agent, a terminal recording or a bug report could carry ([ADR 0027](0027-nothing-is-sent-home.md)).
- One way to do a thing: modules get secrets the way they get everything else, injected by type
  ([ADR 0005](0005-dependency-injection-by-type.md)).
- Headless Linux machines and CI have no keychain, and must still work.
- `hz system doctor` answers "what is missing and what do I do" ([ADR 0015](0015-health-checks-as-lets.md)).

## Considered Options

- **A `Credentials` service over the OS keychain (`keyring`), with an environment fallback** (chosen)
- **Per-plugin token files** (for example `~/.config/hz/github.token`, mode 0600): plaintext on disk, copied by
  backups and dotfile repositories, and every plugin reimplements reading, permissions and doctor checks.
- **Environment variables only**: no storage at all, so secrets end up in shell profiles in plaintext, and a
  background service started by launchd or systemd does not see the shell's environment.
- **An encrypted file owned by Heinzelmännchen**: needs a key, which must live somewhere (the keychain again, or a
  passphrase prompt that breaks unattended runs); we would own a crypto format and its mistakes.

## Decision Outcome

Chosen option: **a `Credentials` service over the OS keychain, with an environment fallback**, because the keychain is
the place each operating system built for this, and a service injected by type keeps modules simple and testable.

- **The only way in.** `heinzel.Credentials` is a service injected by type. Modules and plugins read secrets only
  through it (`get`, `has`, `require`); `set` and `delete` exist for `hz system credential`. They never read token
  files or secret environment variables themselves.
- **Keys.** A credential is keyed by service and name. The service defaults to the qualified name of the declaring
  module (`github`, `system.credential`); a module may name another service to share one credential. In the keychain
  the entry is `service = "heinzel/<service>"`, `username = <name>`, so nothing collides with other applications.
- **Backend.** `keyring` picks the platform's keychain (macOS Keychain, Windows Credential Locker, Secret Service on
  Linux). When `keyring` has no usable backend (its `fail` or `null` backend, typically headless Linux), credentials
  come from environment variables `HZ_CREDENTIAL_<SERVICE>_<NAME>` (upper case, every other character `_`), and
  doctor reports the fallback as `degraded`. The setting `[system.credential] backend` (`auto`, `keyring`,
  `environment`) forces one. There is never a plaintext file.
- **Declarations.** A module declares what it needs: `token = module.credential("api-token", purpose="...")` adds a
  `CredentialLet` (name, purpose, optional service). `Credentials` refuses names nobody declared, so the declarations
  are complete. `hz system credential list` and doctor read them without running module code.
- **Values never leave.** No operation, log line, error message or `hz system info` section contains a value: `list`,
  `set` and `remove` return name, service, purpose, backend and presence only. `set` reads the value from standard
  input or a hidden prompt, never from the command line, and is a CLI-only operation, so no MCP or REST payload
  carries a secret.
- **Needs attention, not an error.** A missing declared credential is a `degraded` check with the command that fixes
  it. A credential the remote service rejects or that expired is the same normal state: the module reports it through
  `Credentials.attention(...)`, a `degraded` check with the same fix, or raises a `HeinzelError` that says how to
  replace it, never a crash.

`CredentialLet` is a new let kind rather than a list inside the service, because declarations must be visible before
any code runs (like settings, [ADR 0012](0012-layered-configuration.md)), owned by the module that declares them, and
consumed by a built-in module (`system.credential`: `list` and its health check), the precedent
[ADR 0015](0015-health-checks-as-lets.md) set for `HealthLet`.

### Consequences

- Good: plugins get secrets in one line, tests pass a memory backend, and a person sees what is missing in doctor.
- Good: macOS and Windows users store secrets where their other secrets are, protected by their login.
- Bad: a keychain cannot be enumerated, so `list` shows declared credentials only; a value stored for a module that
  is no longer installed stays in the keychain until removed with the keychain's own tools.
- Bad: on Linux without Secret Service, secrets live in environment variables, which other processes of the same user
  can read; doctor says so. `set` and `remove` cannot change the environment and say what to export instead.
- Bad: `HZ_CREDENTIAL_A_B_C` is ambiguous between service `a-b` + name `c` and service `a` + name `b-c`; names are
  chosen by module authors, so we accept it.
- Neutral: a new runtime dependency, `keyring` (MIT), with its platform helpers.

### Confirmation

`tests/unit/kernel/credentials_test.py` (keys, the environment backend, refusing undeclared names),
`tests/unit/modules/credential_service_test.py` (backend selection, keychain errors without values) and
`tests/integration/credential_test.py`, which sets a credential and asserts that its value is absent from the JSON
output of every `hz system credential` operation, `hz system doctor` and `hz system info`. The `heinzel.testing`
pytest plugin forces the environment backend, so no test touches a real keychain. `tests/unit/public_api_test.py`
snapshots `Credentials` and `CredentialLet` in the public API.

## More Information

- Decided by the director under the maintainer's standing delegation for technical choices, 2026-10-24.
- [ADR 0020](0020-daemon-head.md) §10 (secrets through `keyring`); [ADR 0028](0028-integrations-are-capability-adapters.md)
  (adapters request credentials per access mode); [SECURITY.md](../../SECURITY.md) lists the measure.
- Revisit when OAuth flows arrive: refresh tokens are credentials too, but the flow that obtains them is not decided
  here.
