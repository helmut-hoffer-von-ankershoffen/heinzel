# Configuration reference

Generated from the code by `make docs`; CI fails when it is out of date. Settings come from TOML files — site, user
(`hz system config init` writes one), the nearest `hz.toml`, `$HZ_CONFIG` — and environment variables, which win.
`hz system config show` prints the values in effect; `hz system config set KEY VALUE` changes them.
Plugins add their own tables.

## `[hz]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `data_dir` | `HZ_DATA_DIR` | platform-specific | Where Heinzelmännchen keeps its local state. |
| `cache_dir` | `HZ_CACHE_DIR` | platform-specific | Where Heinzelmännchen keeps caches that can be deleted at any time. |
| `state_dir` | `HZ_STATE_DIR` | platform-specific | Where Heinzelmännchen keeps runtime state such as locks and the daemon's pause flag. |
| `log_dir` | `HZ_LOG_DIR` | platform-specific | Where background work such as the daemon writes its logs. |
| `log_level` | `HZ_LOG_LEVEL` | `'WARNING'` | Log level for Heinzelmännchen and its modules. |
| `output_format` | `HZ_OUTPUT_FORMAT` | `'text'` | Default output of every operation; `json` suits scripts and agents. `--format` overrides it. |
| `plugins_enabled` | `HZ_PLUGINS_ENABLED` | `True` | Discover modules from installed plugins. Disable to debug a broken plugin. |
| `disabled_modules` | `HZ_DISABLED_MODULES` | `frozenset()` | Qualified names of modules to switch off, with their submodules. Comma-separated in the environment, a list in TOML. |

## `[clock]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `zones` | `HZ_CLOCK__ZONES` | `['Europe/Berlin', 'America/New_York', 'America/Los_Angeles']` | The team's time zones, as IANA names. |
| `working_hours` | `HZ_CLOCK__WORKING_HOURS` | `(9, 18)` | Start and end hour of a working day. |

## `[system.agent]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `skill_dirs` | `HZ_SYSTEM_AGENT__SKILL_DIRS` | platform-specific | Directories `hz system agent install` copies Heinzelmännchen's skills into, one per coding agent. |

## `[system.daemon]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `unit_dir` | `HZ_SYSTEM_DAEMON__UNIT_DIR` | platform-specific | Where `install` writes the launchd LaunchAgent (macOS) or the systemd user units (Linux). |

## `[system.plugin]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `github_token` | `HZ_SYSTEM_PLUGIN__GITHUB_TOKEN` | `None` | GitHub token for `hz system plugin search`, which works without one at a lower rate limit; also read from GH_TOKEN or GITHUB_TOKEN. Sent to api.github.com only. |
