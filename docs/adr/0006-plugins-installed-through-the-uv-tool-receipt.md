---
status: accepted
date: 2026-10-23
implementation: built
amended-by: 0047, 0078
---

# 0006: Plugins are installed through the uv tool receipt

## Context and Problem Statement

Heinzelmännchen is installed with `uv tool install`. We verified that packages installed into a tool environment
with `uv pip install` are removed by `uv tool upgrade`, while requirements added with `--with` are recorded in the
tool's `uv-receipt.toml` and survive upgrades. How do plugins get installed so that they survive upgrades?

## Considered Options

- **Reinstall the tool with every plugin as a `--with` requirement** (chosen)
- **`uv pip install` into the tool environment**: lost on upgrade.
- **Our own plugin directory on `sys.path`**: bypasses dependency resolution.

## Decision Outcome

Chosen option: **plugins are `--with` requirements in the uv tool receipt**, because uv then keeps them across
upgrades and resolves their dependencies.

- `hz system plugin add <spec>` reads the receipt of the environment Heinzelmännchen runs in, and reinstalls the
  tool with `uv tool install --force <heinzel requirement> --with <each plugin> --with <spec>`. Adding a plugin again
  replaces the older entry for the same distribution. `hz system plugin remove` reinstalls without it.
- `hz system update` keeps every plugin requirement as the receipt has it. `uv tool upgrade` leaves a git requirement
  at its ref, so a core pinned to a release tag (`@vX.Y.Z`) is reinstalled from the tag of the highest release in
  SemVer order (pre-releases only if one is installed). A core pinned to a branch, a commit or a local source is left
  alone with the `--source` that moves it; one that follows a default branch or the package index is upgraded by uv.
  If a plugin doesn't allow the new core, the error names it; uv replaces the environment only after resolving, so
  the previous one keeps working.
- Outside a tool environment (development checkouts), Heinzelmännchen falls back to `uv pip install`.
- Plugins depend on `hz` without a source; inside the tool environment the requirement resolves to the installed
  Heinzelmännchen.

### Consequences

- Good: one command to install Heinzelmännchen, one to add a plugin, and both survive upgrades.
- Bad: the receipt format is uv's, so we depend on it.

### Confirmation

`tests/unit/kernel/installation_test.py::test_requirement_from_receipt`, `tests/unit/modules/plugin_service_test.py`
(`test_add_in_tool_env_reinstalls_with_plugins`, `test_add_replaces_older_copy_of_same_plugin`,
`test_outside_tool_env_uses_uv_pip`), `tests/unit/modules/system_update_test.py` and
`tests/integration/update_test.py` (`hz system update` per pin, a blocking plugin) and the e2e test of the full lifecycle on macOS and Linux,
`tests/e2e/tool_install_test.py::test_direct_lifecycle`.
