---
status: proposed
date: 2026-10-31
decision-makers: maintainer
amends: 0032, 0110
related: 0003, 0015, 0026, 0030, 0082, 0094
---

# 0112: Modules declare external tool prerequisites

## Context and Problem Statement

The standard app needs Git and the GitHub CLI (`gh`) for team repositories, plugin sources and state persistence, but
its installer provisions only uv and a managed Python. Other modules check external tools in separate ways, each with
its own installation hint. The maintainer requires Git and `gh` to be installed automatically through Homebrew on
macOS and explained on Linux, the other tools to be requested only by the feature that needs them, and every plugin's
documentation to list its prerequisites. Installing a Python package, installing a program and signing in to an account
are three different things that must not be mixed up, and plugins must stay independently deliverable
([ADR 0094](0094-plugins-own-features-and-independent-delivery.md)).

## Decision Drivers

- One small declaration supplies installation, setup, doctor and documentation, so they cannot drift apart.
- Core and plugins use the same public extension point. Provider names and recipes stay with their owners, so the
  generic engine, the installer and `hz setup` contain no branch naming a vendor.
- A plugin adds a prerequisite without a Core edit and tests it against the released SDK.
- Inspection and offline start stay free of package installation and authentication.
- Tests never start the real Homebrew of the machine they run on.

## Considered Options

- **Public prerequisite declarations, one generic engine, presteps before each owner's existing setup step** (chosen).
- **A central Core table of every provider's tools**: puts concrete plugins into Core and couples their releases.
- **A prerequisite wrapped as a synthesized `SetupLet`**: a module has one setup step by name, so the wrapper would
  displace or duplicate the owner's own step. Rejected.
- **Bespoke installer scripts and README text per plugin**: keeps the duplicated checks and the drift.

## Decision Outcome

Chosen option: **immutable public declarations consumed by one prerequisite service, run as presteps before the
owner's own setup step**, because ownership belongs to the module while installing and reporting are shared.

- **Declaration.** `Module.prerequisite(name, purpose=, linux=, macos=, when=, ...)` adds a `PrerequisiteLet` named
  after the executable. It carries the purpose, a version probe (`--version`, `-version`, `-V` or `version` plus plain
  words, so a declaration cannot make a check run code), an optional minimum version, a
  `Homebrew` formula or cask (or `ManualInstall` text) for macOS, a `ManualInstall` for Linux, and a context:
  `install` (every standard app), `setup` (the owner's setup) or `feature` (only when selected). An optional
  `needed` predicate, evaluated only by checks and installs, takes settings or the capability view and returns whether
  the person's selection needs the tool; `condition` explains it in words and is what documentation shows. Exports
  never evaluate the predicate, probe a tool or run a package manager.
- **Selection.** A context includes its own declarations and those of the narrower contexts (`install` < `setup` <
  `feature`). A `feature` declaration without a predicate is requested only by `--context feature`; one with a predicate joins
  `setup` when it holds. Declared text is one plain line, links are ASCII HTTPS, and nothing but the executable's
  own name is ever run. A check selects the declarations of one named module, or of every enabled module.
  Core-only bootstrap discovers no plugin, so the installer sees only Core's declarations.
- **Service.** `PrerequisiteService` (`list`, `check`, `install`) is implemented by `system.prerequisite`.
  Checks run bounded local version probes (`--version`), no network, no account. A tool is ready only when its probe
  succeeds and meets the minimum version: an exit-zero package manager, a stale path or Apple's developer-tool stub
  (never run while the Command Line Tools are missing, since that would open a GUI installer) do not count.
  Conflicting recipes for one executable are reported and nothing is installed for it; a predicate that fails is a
  finding, never permission to install.
- **Install.** On macOS with Homebrew present, `install` runs fixed `brew install --formula|--cask` argument lists
  without auto-update or upgrade of installed packages, then probes again; the new tool's directory is added to
  `PATH` for the running process so the continuing flow needs no shell restart. Without Homebrew, on Linux and on
  other systems, it changes nothing and returns the owner's instructions with the outcome `manual`; no installation
  script is run and no package manager is bootstrapped. Existing incompatible versions get update guidance;
  upgrades are a separate decision. Package-manager-less Linux installation is out of scope.
- **The installer.** After resolving the profile and before downloading wheels, `uvx heinzel` / `hz-install` checks
  the `install` context of the Core-only kernel. It names the missing tools and their owners and, on macOS with
  Homebrew, installs them (running the installer is the person's yes; `--no-tools`, `--dry-run` and `--python` only
  check). Otherwise it prints the instructions. A manual
  outcome never fails or repeats the app installation; the installer repeats what is pending at its end.
  `--dry-run` only checks.
- **Setup.** Before an owner's setup step runs, `hz setup` checks that owner's `setup` context. It shows what is
  missing and, with a person at the terminal, asks before installing; without one (answers file, no terminal) it
  installs nothing and prints the equivalent command. A step whose prerequisites remain unmet does not run and is
  reported as failed with the instructions, so it is offered again. Owners that declare prerequisites but have no
  setup step get one leading stage reported as `pending` without failing the run. Authentication is never part of
  a prestep: signing in stays the owner's own step, and doctor and checks never sign in.
- **Doctor and CLI.** `hz system prerequisite list|check|install` (`list` is public, `check` private, `install` needs
  `--confirm` and is CLI-only) and a doctor check report the same outcomes. Removing a plugin never removes an external tool: other modules may use it.
- **Documentation.** A declared prerequisite appears in the plugin's generated page (ADR 0082) as a table of
  executable, purpose, condition, minimum version, macOS package and Linux steps, linked to the owner's
  authentication step. `hz system prerequisite list --json` is the same export for catalogs and agents. The page and
  the checks read the one declaration; `hz system plugin docs build --check` rejects a stale page.
- **Core's own declarations.** `system` declares Git and `gh` (`install`). `system.runner` declares the Claude Code and
  Codex CLIs, each only while its runner is the one selected. `system.notify` declares `notify-send` on a Linux desktop
  that shows notifications. Plugins declare theirs (for example a VPN's CLI when sharing is selected); Core names none.
  `launchctl`, `osascript` and `systemctl` are operating-system facilities, and build tools (Node, pnpm) are not
  runtime prerequisites.

### Consequences

- Good: a person sees what is missing, who needs it and what will be installed, before anything is installed.
- Good: a plugin author maintains one declaration; docs, doctor, setup and the installer follow it.
- Good: nothing in the generic path names a vendor, so plugins add prerequisites without a Core release.
- Bad: tool readiness is a third state next to "package installed" and "signed in"; heads must keep them apart.
- Bad: Homebrew's own dependencies and permissions stay external; a stale local formula index can make a
  declared formula fail until `brew update` is run, which the failure explains.

### Confirmation

- `tests/unit/kernel/prerequisites_test.py`: declaration validation, `Module.prerequisite`, duplicate names, the
  report's unmet list, adapter-only modules that declare one.
- `tests/unit/modules/prerequisite_service_test.py` with `tests/_prerequisite_machine.py`: the engine against described
  machines. Missing, compatible, incompatible and conflicting tools; Homebrew install and re-probe; Linux and
  missing-Homebrew manual outcomes; selection by context and predicate; Apple's stub; `PATH`. `tests/conftest.py`
  replaces the real machine for the whole suite, so no test reaches the real Homebrew (`test_the_suite_describes_the_real_machine_as_a_linux_host_that_refuses_homebrew`).
- `tests/unit/modules/prerequisite_operations_test.py`: list, check, install (CLI only, `--confirm`), doctor.
- `tests/unit/modules/core_prerequisites_test.py`: what Core declares, and that the generic engine, the installer
  prestep and `hz setup` name no vendor.
- `tests/unit/heads/cli_setup_test.py` and `tests/integration/setup_test.py`: tools before the existing `SetupLet`,
  consent, nothing installed without a person, a blocked step, owners without a step.
- `tests/unit/heads/prerequisites_test.py`: the installer's prestep, manual outcomes that do not fail it, `--dry-run`.
- `tests/unit/modules/plugin_docs_test.py`: the generated prerequisites section; `tests/unit/public_api_test.py`.
- Real Homebrew on a Mac remains a separately coordinated acceptance trial; a fake host is not reported as that proof.

## More Information

- [Homebrew installation](https://docs.brew.sh/Installation) and [command behavior](https://docs.brew.sh/Manpage).
- [GitHub CLI credential-helper setup](https://cli.github.com/manual/gh_auth_setup-git) stays separate from installing
  the executable.
- Audit at the time of writing of the tools plugins and Core use, by owner: Git and `gh` (baseline, Core); Claude Code and Codex (the
  selected runner, Core); Tailscale (sharing, its plugin); `acli` (Atlassian plugin); `gws` (Google plugin, a native
  Homebrew formula, so no Node); `notify-send` (Core, Linux). Zoom needs no CLI. Plugins adopt the released SDK in
  their own repositories and releases.
- Amends [ADR 0032](0032-modules-declare-interactive-setup-steps.md) (steps may be preceded by prerequisite presteps)
  and [ADR 0110](0110-one-versionless-launcher-installs-and-opens-the-standard-app.md) (the launcher's installer
  prepares the baseline tools).
