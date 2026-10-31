---
status: accepted
date: 2026-10-24
decision-makers: maintainer (delegated)
related: 0005, 0012, 0015, 0018, 0020, 0027, 0028
---

# 0030: Credentials live in the OS keychain behind a credentials service

## Context and Problem Statement

Plugins and the planned capability adapters ([ADR 0028](0028-integrations-are-capability-adapters.md)) need API tokens
and similar secrets, and the daemon ([ADR 0020](0020-daemon-head.md) §10) already says secrets come from the OS
keychain through `keyring`, never from unit files. ADRs 0020 and 0028 are still proposed; this record stands on its
own and holds whatever becomes of them. Without one way to store and read secrets, each plugin invents its own: a
token file here, an environment variable there, a value in `config.toml` that ends up in a team repository. How do
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

- **The only way in.** `heinzel.Credentials` is a service injected by type. A module reads secrets only through it,
  and only by passing the `CredentialLet` it declared: `credentials.get(token)`, `has(token)`, `require(token)`.
  Finding a credential by name and service is for `hz system credential` alone (its `CredentialService`). Modules
  never read token files or secret environment variables themselves.
- **Not an isolation boundary.** Services are a naming scheme. Every module and plugin runs in the same process with
  the user's rights, so a plugin can read any credential: through `Credentials`, through `keyring` directly, or by
  registering a keyring backend; on macOS any script the same Python runs can read the `heinzel/*` entries. Only
  install plugins you trust ([SECURITY.md](../../SECURITY.md)). The let-only API states intent and makes misuse
  visible in review; it does not prevent it.
- **Keys.** A credential is keyed by service and name. The service defaults to the qualified name of the declaring
  module (`github`, `system.credential`); a module may name another qualified module name as service to share one
  credential, and `module.credential` rejects anything else. In the keychain the entry is
  `service = "heinzel/<service>"`, `username = <name>`, so nothing collides with other applications.
- **Backend: OS keychains only, never a plaintext file.** Under the default `[system.credential] backend = "auto"`,
  `keyring` is used only when it chose an operating system's keychain: macOS Keychain, Windows Credential Locker,
  Secret Service or KWallet, or a chainer of only those. Any other backend (its `fail` or `null` placeholder, a
  plaintext backend such as keyrings.alt's, one a plugin registers or `PYTHON_KEYRING_BACKEND` or `keyringrc.cfg`
  selects) counts as no keychain. Then credentials come from environment variables `HZ_CREDENTIAL_<SERVICE>_<NAME>`
  (upper case, every other character `_`), and doctor reports the fallback as `degraded`. `backend = "keyring"`
  forces whatever keyring chose, and doctor reports a non-OS backend as `degraded`; `backend = "environment"` forces
  the variables. The kernel never imports `keyring`; the backend lives in `system.credential`, which is essential
  (it can't be disabled), because plugins depend on the public `Credentials` type it provides.
- **Declarations.** A module declares what it needs: `token = module.credential("api-token", purpose="...")` adds a
  `CredentialLet` (name, purpose, optional service). `Credentials` refuses lets no registered module declared, so the
  declarations are complete. `hz system credential list` and doctor read them without running module code; doctor
  names each credential `credential <service>/<name>` once, and reports declared credentials whose variable names
  collide in the environment fallback (`foo.bar` and `foo-bar` both become `FOO_BAR`).
- **Values never leave.** No operation, log line, error message or `hz system info` section contains a value: `list`,
  `set` and `remove` return name, service, purpose, backend, presence and whether something was removed. Backend
  errors are re-raised as `HeinzelError` without the value or the original exception. `set` reads the value from
  standard input or a hidden prompt, never from the command line; `set` and `remove` are CLI-only operations, so no
  MCP or REST request carries a value or removes one. Programs Heinzelmännchen starts (uv, git, copier) get an
  environment without `HZ_CREDENTIAL_*` variables.
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
- Bad: credentials are not isolated between plugins; a malicious plugin can read all of them, as it can read every
  file the user can. The protection is choosing plugins ([SECURITY.md](../../SECURITY.md)), not this service.
- Bad: keychains are not portably enumerable through `keyring`, so `list` shows declared credentials only; a value
  stored for a module that is no longer installed stays in the keychain until removed with the keychain's own tools.
- Bad: on Linux without Secret Service, secrets live in environment variables, which other processes of the same user
  can read; doctor says so. `set` and `remove` cannot change the environment and say what to export instead.
- Bad: `HZ_CREDENTIAL_A_B_C` is ambiguous between service `a-b` + name `c` and service `a` + name `b-c`; doctor reports
  such collisions among declared credentials.
- Neutral: a new runtime dependency, `keyring` (MIT), with its platform helpers.

### Confirmation

- `tests/unit/kernel/credentials_test.py`: lets only, the environment backend, redacted backend errors, the
  subprocess environment.
- `tests/unit/modules/credential_service_test.py`: only OS keychains under `auto` (with a stand-in plaintext
  backend), a forced non-OS backend is degraded, lookup by name and service, collision and naming checks.
- `tests/integration/credential_test.py`: sets a credential in the memory and the environment backend and asserts its
  value is absent from every `hz system credential`, `doctor`, `info`, `modules` and `config show` output, from the
  MCP and REST heads and from logs, also when a backend's errors carry the value; `set` and `remove` are CLI-only.
- `tests/unit/kernel/kernel_test.py::test_a_plugin_sorting_before_a_builtin_cannot_take_over_its_service`.
- The import-linter contract "The kernel never imports keyring" (`pyproject.toml`).
- The `heinzel.testing` pytest plugin forces the environment backend, so no test touches a real keychain;
  `tests/unit/public_api_test.py` snapshots `Credentials` and `CredentialLet` in the public API.

## More Information

- [ADR 0020](0020-daemon-head.md) §10 (secrets through `keyring`); [ADR 0028](0028-integrations-are-capability-adapters.md)
  (adapters request credentials per access mode); [SECURITY.md](../../SECURITY.md) lists the measure.
- Known exception: `system.plugin` reads its optional GitHub token from settings (`GH_TOKEN`, `GITHUB_TOKEN`); moving it
  behind `Credentials` is a follow-up.
- Revisit when OAuth flows arrive: refresh tokens are credentials too, but the flow that obtains them is not decided
  here.
