---
status: accepted
date: 2026-10-23
implementation: built
---

# 0012: Layered configuration with per-module settings

## Context and Problem Statement

Users must be able to override every default — per machine, per user, per team repository and per invocation —
and modules (including plugins) need typed settings without inventing their own configuration files.

## Considered Options

- **Layered TOML files plus environment variables, one table per module** (chosen)
- **pydantic-settings' `TomlConfigSettingsSource` per model**: no merging across scopes, no per-module tables.
- **YAML/INI**: TOML is Python's native configuration format and needs no dependency (`tomllib`).

## Decision Outcome

Chosen option: **layered configuration with per-module settings**, because every default can be overridden at every
scope and plugins get typed settings for free.

- Configuration files are TOML, in precedence order: `site` (platformdirs' site config dir), `user`
  (`$HZ_CONFIG_HOME`, else `$XDG_CONFIG_HOME/hz`, else platformdirs' user config dir), `project` (the nearest
  `hz.toml` in the working directory or above), `explicit` (`$HZ_CONFIG`). Tables are merged deeply.
- Environment variables override files: `HZ_<FIELD>` for the kernel, `HZ_<MODULE>__<FIELD>` for modules.
- Kernel settings are the `[hz]` table; a module's settings are the table named by its qualified name.
- A module declares settings with `@module.settings` on a `ModuleSettings` subclass (pydantic-settings). The kernel
  builds them and injects them by type, like services (ADR 0005).
- Directories for data and cache come from platformdirs and are settings themselves.
- `hz system config show` explains where every value comes from; `hz system config init` writes a commented
  starter file.

### Consequences

- Good: plugins get configuration for free and document it through field descriptions.
- Neutral: `.env` files are deliberately not read: one less place for values to hide.

### Confirmation

`tests/unit/kernel/config_test.py` (`test_config_files_in_precedence_order`, `test_load_merges_files_deeply`,
`test_module_settings_layering`, `test_user_config_dir_precedence`),
`tests/unit/kernel/kernel_test.py::test_injects_module_settings_from_the_module_section` and
`tests/integration/cli_test.py::test_config_layers_files_and_environment`.
