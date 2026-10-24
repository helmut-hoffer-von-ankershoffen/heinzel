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
| `update_check` | `HZ_UPDATE_CHECK` | `True` | Say in `hz --version` and `hz system info` when a newer release is available, from what `hz system release` last found (ADR 0031); only in text output on a terminal, never in CI. Ordinary commands never ask the network. Set to false to turn the hint off. |
| `disabled_modules` | `HZ_DISABLED_MODULES` | `frozenset()` | Qualified names of modules to switch off, with their submodules. Comma-separated in the environment, a list in TOML. |

## `[capabilities]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `agent_runner` | `HZ_CAPABILITIES__AGENT_RUNNER` | `'claude-code'` | Adapter that serves `agent-runner`: `claude-code` (the default, because `command` needs a command line first), `command`, or one from a plugin. |
| `code_host` | `HZ_CAPABILITIES__CODE_HOST` | `None` | Adapter that serves `code-host`, e.g. `github`. |
| `documents` | `HZ_CAPABILITIES__DOCUMENTS` | `None` | Adapter that serves `documents`. |
| `tracker` | `HZ_CAPABILITIES__TRACKER` | `None` | Adapter that serves `tracker`, e.g. `github`. |

## `[clock]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `zones` | `HZ_CLOCK__ZONES` | `['Europe/Berlin', 'America/New_York', 'America/Los_Angeles']` | The team's time zones, as IANA names. |
| `working_hours` | `HZ_CLOCK__WORKING_HOURS` | `(9, 18)` | Start and end hour of a working day. |

## `[system.agent]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `skill_dirs` | `HZ_SYSTEM_AGENT__SKILL_DIRS` | platform-specific | Directories `hz system agent install` copies Heinzelmännchen's skills into, one per coding agent. |

## `[system.credential]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `backend` | `HZ_SYSTEM_CREDENTIAL__BACKEND` | `'auto'` | Where credentials live. `auto`: the OS keychain when there is one, else the environment. `keyring`: whatever keyring backend is configured, even one that is not an OS keychain. `environment`: read-only HZ_CREDENTIAL_<SERVICE>_<NAME> variables. |

## `[system.daemon]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `unit_dir` | `HZ_SYSTEM_DAEMON__UNIT_DIR` | platform-specific | Where `install` writes the launchd LaunchAgent (macOS) or the systemd user units (Linux). |

## `[system.plugin]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `github_token` | `HZ_SYSTEM_PLUGIN__GITHUB_TOKEN` | `None` | GitHub token for `hz system plugin search`, which works without one at a lower rate limit; also read from GH_TOKEN or GITHUB_TOKEN. Sent to api.github.com only. |

## `[system.runner]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `model` | `HZ_SYSTEM_RUNNER__MODEL` | `None` | Model for every run, e.g. `sonnet`; the agent's own default when not set. |
| `claude_executable` | `HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE` | `'claude'` | The Claude Code CLI, a name on PATH or a path. |
| `read_only_tools` | `HZ_SYSTEM_RUNNER__READ_ONLY_TOOLS` | `['Read', 'Grep', 'Glob']` | Claude Code tools a run may use; a profile asking for anything else is refused. Add only tools that cannot change anything. |
| `command` | `HZ_SYSTEM_RUNNER__COMMAND` | platform-specific | The `command` runner's command line, one argument per item, never through a shell. Placeholders: {prompt_file}, {contract_file}, {output_file}, {working_dir}, {tools}, {model}, {timeout_seconds}, {max_usd}; `{{` and `}}` are literal braces; e.g. ["mytool", "ai", "run", "--prompt", "{prompt_file}"]. |
| `command_output` | `HZ_SYSTEM_RUNNER__COMMAND_OUTPUT` | `'stdout'` | Where the `command` runner reads the result: stdout, or {output_file}. |
| `command_format` | `HZ_SYSTEM_RUNNER__COMMAND_FORMAT` | `'text'` | `text`: the output is the draft. `json`: an object with the draft under `command_draft_key` and, optionally, `usage` (input_tokens, output_tokens) and `total_cost_usd`. |
| `command_draft_key` | `HZ_SYSTEM_RUNNER__COMMAND_DRAFT_KEY` | `'result'` | Key of the draft in `json` output. |
| `command_enforces_read_only` | `HZ_SYSTEM_RUNNER__COMMAND_ENFORCES_READ_ONLY` | `False` | The `command` runner refuses to run until this is true: set it once the configured command restricts its agent to read-only tools itself. |
| `command_read_only_tools` | `HZ_SYSTEM_RUNNER__COMMAND_READ_ONLY_TOOLS` | platform-specific | Tools, in the command's naming, a tool profile may ask the `command` runner for; others are refused. |
| `command_env` | `HZ_SYSTEM_RUNNER__COMMAND_ENV` | platform-specific | Environment variables passed to the command besides PATH, HOME, LANG, LC_* and TMPDIR, e.g. the variable that holds its own login; HZ_CREDENTIAL_* variables are never passed. |

## `[workflow]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `user_dir` | `HZ_WORKFLOW__USER_DIR` | platform-specific | Your own contracts, `<name>.prose.md`; they run on your daemon when they have a schedule. |
| `team_repository` | `HZ_WORKFLOW__TEAM_REPOSITORY` | `None` | A checkout of your team's state repository; its contracts are in `workflows/`. They run only with `hz workflow run`, or on your daemon when named in `schedule_team`. |
| `schedule_team` | `HZ_WORKFLOW__SCHEDULE_TEAM` | platform-specific | Team contracts your daemon runs on their schedule, by name; the others run only by hand, so a team's contracts don't run on every member's daemon. |
