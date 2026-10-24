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
| `expert` | `HZ_EXPERT` | `False` | Expert mode: list every command in help, shell completion and the web app, not just the everyday ones. Hidden commands run all the same, and MCP and REST always list everything. Change it with `hz system expert on` or `off`; not settable in a project's hz.toml (ADR 0041). |
| `plugins_enabled` | `HZ_PLUGINS_ENABLED` | `True` | Discover modules from installed plugins. Disable to debug a broken plugin. |
| `update_check` | `HZ_UPDATE_CHECK` | `True` | Check for a newer release at most once an hour, in the background with a 2-second timeout, and say so on stderr after human output (ADR 0031): one unauthenticated request that carries nothing about you. A failed check waits an hour too. Never in JSON, MCP or REST output, and off in CI. Set to false to turn the check and the notice off; not settable in a project's hz.toml. |
| `update_channel` | `HZ_UPDATE_CHANNEL` | `'release'` | What `hz system update` follows: `release` moves to the newest release, `main` to the newest commit of the default branch. An installation on `main` moves to releases once one is as new as it. |
| `disabled_modules` | `HZ_DISABLED_MODULES` | `frozenset()` | Qualified names of modules to switch off, with their submodules. Comma-separated in the environment, a list in TOML. |
| `ui_port` | `HZ_UI_PORT` | `8737` | Port `hz ui` serves the web app on, on 127.0.0.1 only. It stays the same so an installed app (a Dock icon) keeps its address; `hz ui --port 0` picks a free one for this session. Not settable in a project's hz.toml (ADR 0046). |
| `api_allowed_hosts` | `HZ_API_ALLOWED_HOSTS` | `frozenset()` | Host names `hz system serve api` answers besides localhost, 127.0.0.1 and [::1], for when it listens on another interface (`--host`); requests naming any other host are refused, so a web page can't reach the server through DNS rebinding. Comma-separated in the environment; not settable in a project's hz.toml. |

## `[capabilities]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `agent_runner` | `HZ_CAPABILITIES__AGENT_RUNNER` | `'claude-code'` | Adapter that serves `agent-runner`: `claude-code` (the default, because `command` needs a command line first), `command`, or one from a plugin. |
| `ai_access` | `HZ_CAPABILITIES__AI_ACCESS` | `'claude-code'` | How hz reaches AI, the AI access in use: `claude-code` (the default), `gateway` (a company CLI configured in `[ai.access]`), `pydantic-ai`, or one from a plugin; `hz ai access use` sets it (ADR 0057). |
| `calendar` | `HZ_CAPABILITIES__CALENDAR` | `None` | Adapter that serves `calendar`, e.g. `google`. |
| `chat` | `HZ_CAPABILITIES__CHAT` | `None` | Adapter that serves `chat`, e.g. `slack`. |
| `code_host` | `HZ_CAPABILITIES__CODE_HOST` | `None` | Adapter that serves `code-host`, e.g. `github`. |
| `directory` | `HZ_CAPABILITIES__DIRECTORY` | `'one-on-ones'` | Adapter that serves `directory`: `one-on-ones` (the default, the core's guesses from recurring 1:1s in the bound calendar, each marked as a guess for the user to confirm), or one from a plugin that reads a directory of record. |
| `documents` | `HZ_CAPABILITIES__DOCUMENTS` | `None` | Adapter that serves `documents`. |
| `mail` | `HZ_CAPABILITIES__MAIL` | `None` | Adapter that serves `mail`, e.g. `google`. |
| `meetings` | `HZ_CAPABILITIES__MEETINGS` | `None` | Adapter that serves `meetings`, e.g. `zoom`. |
| `tracker` | `HZ_CAPABILITIES__TRACKER` | `None` | Adapter that serves `tracker`, e.g. `github`. |
| `team_graph` | `HZ_CAPABILITIES__TEAM_GRAPH` | `'hz-graph'` | Adapter that serves `team-graph`: `hz-graph` (the default, the core's graph of your team's contracts and telemetry), or one from a plugin. |

## `[ai.access]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `gateway_title` | `HZ_AI_ACCESS__GATEWAY_TITLE` | `'Company AI gateway'` | What people pick the gateway by, e.g. `Acme AI`. |
| `gateway_summary` | `HZ_AI_ACCESS__GATEWAY_SUMMARY` | `"Your company's AI gateway CLI, set up in [ai.access]."` | One sentence on how the gateway reaches AI. |
| `gateway_status_command` | `HZ_AI_ACCESS__GATEWAY_STATUS_COMMAND` | platform-specific | Says whether you are signed in without calling a model: exit code 0 when signed in, and optionally a JSON object on stdout, `{"signed_in": true, "models": ["..."], "detail": "..."}`; e.g. `["acme", "ai", "status", "--json"]`. Unset: the gateway isn't set up. |
| `gateway_test_command` | `HZ_AI_ACCESS__GATEWAY_TEST_COMMAND` | platform-specific | One cheap live call, run only by `hz ai access test --name gateway`: exit code 0 when it answered, and optionally a JSON object, `{"ok": true, "detail": "...", "model": "...", "cost_usd": 0.001}`; e.g. `["acme", "ai", "ping"]`. |
| `gateway_sign_in` | `HZ_AI_ACCESS__GATEWAY_SIGN_IN` | `''` | The command that signs you in, shown as the fix, e.g. `acme login`. |
| `gateway_runner` | `HZ_AI_ACCESS__GATEWAY_RUNNER` | `None` | The agent runner that serves the gateway's chat and coding uses, e.g. `command` with `[system.runner] command` set to the gateway's agent CLI; `hz ai access use gateway` binds it. |
| `gateway_hosts` | `HZ_AI_ACCESS__GATEWAY_HOSTS` | platform-specific | Hosts the gateway sends prompts to, as host names or URLs, e.g. `ai.acme.example`; shown in the listing and the capability matrix. |
| `gateway_models` | `HZ_AI_ACCESS__GATEWAY_MODELS` | platform-specific | Models the gateway offers, when its status command doesn't say. |
| `gateway_env` | `HZ_AI_ACCESS__GATEWAY_ENV` | platform-specific | Environment variables passed to the gateway's commands besides PATH, HOME, LANG, LC_* and TMPDIR; HZ_CREDENTIAL_* variables are never passed. |
| `model` | `HZ_AI_ACCESS__MODEL` | `None` | The model of the `pydantic-ai` option, as Pydantic AI names it, `<vendor>:<model>`, e.g. `anthropic:claude-sonnet-4-5`; its key is the credential `ai.access/api-key` or the vendor's variable. |

## `[atlassian]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `site_url` | `HZ_ATLASSIAN__SITE_URL` | `None` | The Atlassian Cloud site acli is logged in to, `https://<site>.atlassian.net`: issue and page links are built on it, and page URLs must name it. Only the site, user and HZ_CONFIG files and the environment can set it, never a project hz.toml. |
| `acli` | `HZ_ATLASSIAN__ACLI` | `'acli'` | Atlassian's CLI: a name looked up on PATH (only absolute PATH entries count) or an absolute path. Never read from a project hz.toml. |
| `timeout` | `HZ_ATLASSIAN__TIMEOUT` | `60.0` | Seconds to wait for each acli command before stopping it. |

## `[clock]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `zones` | `HZ_CLOCK__ZONES` | `[{'name': 'Europe/Berlin', 'hours': [9, 18]}, {'name': 'America/New_York', 'hours': [9, 18]}, {'name': 'America/Los_Angeles', 'hours': [9, 18]}]` | The team's time zones, yours first: IANA names, or tables with hours, e.g. { name = "Asia/Tokyo", hours = [10, 19] }. The first zone is your own (primary) one. |
| `working_hours` | `HZ_CLOCK__WORKING_HOURS` | `None` | Deprecated: the hours of every zone given by name only. Give each zone its hours instead. |

## `[github]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `host` | `HZ_GITHUB__HOST` | `'github.com'` | The GitHub host `gh` talks to: `github.com`, or the host name of a GitHub Enterprise Server such as `ghe.example.com`; passed to `gh` as `--hostname` and `GH_HOST`, so `gh` uses its login for that host. Only the site, user and HZ_CONFIG files and the environment can set it, never a project hz.toml. |
| `gh` | `HZ_GITHUB__GH` | `'gh'` | The GitHub CLI to run: a name looked up on PATH (absolute entries only) or an absolute path. |
| `timeout` | `HZ_GITHUB__TIMEOUT` | `60.0` | Seconds to wait for each `gh` call before stopping it. |

## `[google]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `gws_path` | `HZ_GOOGLE__GWS_PATH` | `None` | Absolute path of the Google Workspace CLI (`gws`) executable; `gws` on PATH when not set. Only the site, user and HZ_CONFIG files and the environment can set it, never a project hz.toml. |
| `timeout` | `HZ_GOOGLE__TIMEOUT` | `60.0` | Seconds to wait for each `gws` run before stopping it. |

## `[graph]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `time_zone` | `HZ_GRAPH__TIME_ZONE` | `None` | The IANA time zone team contracts' schedules are read in, e.g. Europe/Lisbon; your system's when unset. |
| `offline_after` | `HZ_GRAPH__OFFLINE_AFTER` | `'2d'` | How long a Heinzelmännchen may be silent (no entry, not even a heartbeat) before it shows as offline, e.g. 36h. |
| `weeks` | `HZ_GRAPH__WEEKS` | `4` | How many weeks of kept promises the graph counts. |

## `[org]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `handle` | `HZ_ORG__HANDLE` | `None` | Your handle in teams, pull requests and contracts: lowercase letters, digits and dashes, e.g. mira. Your GitHub login is a good one. |
| `role` | `HZ_ORG__ROLE` | `None` | Your role, one of engineer, principal, engineering-manager, engineering-leadership, product-manager, product-leadership, program-manager. |
| `timezone` | `HZ_ORG__TIMEZONE` | `None` | Your IANA time zone, e.g. Europe/Berlin. |
| `working_hours` | `HZ_ORG__WORKING_HOURS` | `(9, 18)` | Start and end hour of your working day, e.g. [9, 18] or "09-18" (HZ_ORG__WORKING_HOURS=09-18). |
| `github` | `HZ_ORG__GITHUB` | `None` | Your GitHub login, needed to approve and own in a team. |
| `coding_agent` | `HZ_ORG__CODING_AGENT` | `None` | The coding agent you draft with: claude-code, command (one of your own) or none. |
| `name` | `HZ_ORG__NAME` | `None` | Your name, from your Google or GitHub login; stays on this machine, never in a repository. |
| `email` | `HZ_ORG__EMAIL` | `None` | Your work email, from your Google login; stays on this machine, never in a repository. |

## `[report]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `user` | `HZ_REPORT__USER` | `None` | Your handle in your team's roster, which every entry you report carries; e.g. costas. |

## `[system.agent]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `skill_dirs` | `HZ_SYSTEM_AGENT__SKILL_DIRS` | platform-specific | Directories `hz system agent install` copies Heinzelmännchen's skills into, one per coding agent. |

## `[system.changelog]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `github_token` | `HZ_SYSTEM_CHANGELOG__GITHUB_TOKEN` | `None` | GitHub token for `hz system changelog --newer`, which works without one at a lower rate limit; also read from GH_TOKEN or GITHUB_TOKEN. Sent to api.github.com only; the hourly release check never sends it. |

## `[system.credential]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `backend` | `HZ_SYSTEM_CREDENTIAL__BACKEND` | `'auto'` | Where credentials live. `auto`: the OS keychain when there is one, else the environment. `keyring`: whatever keyring backend is configured, even one that is not an OS keychain. `environment`: read-only HZ_CREDENTIAL_<SERVICE>_<NAME> variables. |

## `[system.daemon]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `unit_dir` | `HZ_SYSTEM_DAEMON__UNIT_DIR` | platform-specific | Where `install` writes the launchd LaunchAgent (macOS) or the systemd user units (Linux). |

## `[system.directory]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `lookback_days` | `HZ_SYSTEM_DIRECTORY__LOOKBACK_DAYS` | `56` | How many days of the bound calendar, up to now, to look for recurring 1:1s in; at least 42, so that a 1:1 every two weeks can occur three times. |
| `min_occurrences` | `HZ_SYSTEM_DIRECTORY__MIN_OCCURRENCES` | `3` | How often two people must have met alone, weekly or every two weeks, before hz guesses a reporting line between them. |

## `[system.notify]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `enabled` | `HZ_SYSTEM_NOTIFY__ENABLED` | `True` | Record notices at all; false turns every kind off. |
| `draft` | `HZ_SYSTEM_NOTIFY__DRAFT` | `True` | Notify when a draft is ready to read and approve. |
| `failed` | `HZ_SYSTEM_NOTIFY__FAILED` | `True` | Notify when a contract run on your daemon failed. |
| `doctor` | `HZ_SYSTEM_NOTIFY__DOCTOR` | `True` | Notify when `hz system doctor` finds something degraded or down. |
| `release` | `HZ_SYSTEM_NOTIFY__RELEASE` | `False` | Notify when a newer release of Heinzelmännchen is out. |
| `quiet` | `HZ_SYSTEM_NOTIFY__QUIET` | `False` | Quiet mode: record notices in the list, never pop one up, urgent ones included. |
| `working_hours` | `HZ_SYSTEM_NOTIFY__WORKING_HOURS` | `True` | Hold notices that aren't urgent until your working hours ([org] working_hours, Monday to Friday, in [org] timezone) start. |
| `per_hour` | `HZ_SYSTEM_NOTIFY__PER_HOUR` | `6` | At most this many pop-ups an hour; more go to the list only. |
| `repeat_hours` | `HZ_SYSTEM_NOTIFY__REPEAT_HOURS` | `24` | The same notice again within this many hours is dropped. |
| `native` | `HZ_SYSTEM_NOTIFY__NATIVE` | `True` | Let the daemon pop up native notifications (Heinzel Notifier on macOS, notify-send on Linux) when no web app is open. |

## `[system.plugin]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `github_token` | `HZ_SYSTEM_PLUGIN__GITHUB_TOKEN` | `None` | GitHub token for `hz system plugin search` and for reading a public repository's metadata before `add` when the gh CLI isn't installed, which work without one at a lower rate limit; also read from GH_TOKEN or GITHUB_TOKEN. Sent to api.github.com only. |

## `[system.runner]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `model` | `HZ_SYSTEM_RUNNER__MODEL` | `None` | Model for every run, e.g. `sonnet`; the agent's own default when not set. |
| `claude_executable` | `HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE` | `'claude'` | The Claude Code CLI, a name on PATH or a path. |
| `read_only_tools` | `HZ_SYSTEM_RUNNER__READ_ONLY_TOOLS` | `['Read', 'Grep', 'Glob']` | Claude Code tools a run may use; a profile asking for anything else is refused. Add only tools that cannot change anything. |
| `command` | `HZ_SYSTEM_RUNNER__COMMAND` | platform-specific | The `command` runner's command line, one argument per item, never through a shell. Placeholders: {prompt_file}, {contract_file}, {output_file}, {working_dir}, {tools}, {model}, {timeout_seconds}, {max_usd}, {mcp_config} (a JSON file of MCP servers in the `mcpServers` shape, for a chat's hz tools); `{{` and `}}` are literal braces; e.g. ["mytool", "ai", "run", "--prompt", "{prompt_file}"]. |
| `command_output` | `HZ_SYSTEM_RUNNER__COMMAND_OUTPUT` | `'stdout'` | Where the `command` runner reads the result: stdout, or {output_file}. |
| `command_format` | `HZ_SYSTEM_RUNNER__COMMAND_FORMAT` | `'text'` | `text`: the output is the draft. `json`: an object with the draft under `command_draft_key` and, optionally, `usage` (input_tokens, output_tokens) and `total_cost_usd`. |
| `command_draft_key` | `HZ_SYSTEM_RUNNER__COMMAND_DRAFT_KEY` | `'result'` | Key of the draft in `json` output. |
| `command_enforces_read_only` | `HZ_SYSTEM_RUNNER__COMMAND_ENFORCES_READ_ONLY` | `False` | The `command` runner refuses to run until this is true: set it once the configured command restricts its agent to read-only tools itself. |
| `command_read_only_tools` | `HZ_SYSTEM_RUNNER__COMMAND_READ_ONLY_TOOLS` | platform-specific | Tools, in the command's naming, a tool profile may ask the `command` runner for; others are refused. |
| `command_env` | `HZ_SYSTEM_RUNNER__COMMAND_ENV` | platform-specific | Environment variables passed to the command besides PATH, HOME, LANG, LC_* and TMPDIR, e.g. the variable that holds its own login; HZ_CREDENTIAL_* variables are never passed. |
| `ask_max_usd` | `HZ_SYSTEM_RUNNER__ASK_MAX_USD` | `0.25` | Most one answer of `hz system runner ask` or one message of `hz chat` may cost in US dollars. |

## `[system.runner.chat]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `session_max_usd` | `HZ_SYSTEM_RUNNER_CHAT__SESSION_MAX_USD` | `2.0` | Most one chat session may cost in US dollars, all its messages together; a message never costs more than system.runner.ask_max_usd. |
| `keep_days` | `HZ_SYSTEM_RUNNER_CHAT__KEEP_DAYS` | `30` | Days a chat session is kept after its last message, then removed. |
| `keep_sessions` | `HZ_SYSTEM_RUNNER_CHAT__KEEP_SESSIONS` | `50` | Chat sessions kept at most; the oldest beyond are removed. |
| `tools` | `HZ_SYSTEM_RUNNER_CHAT__TOOLS` | `'everyday'` | Which operations the chat's agent can use: `everyday` (the ones `hz --help` lists; every tool is sent with every message, so fewer make each message cheaper) or `all`, every operation expert mode shows. |

## `[system.theme]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `name` | `HZ_SYSTEM_THEME__NAME` | `'default'` | The theme of the web app, one `hz system theme list` shows, e.g. `acme`; `default` is built in. |

## `[team]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `repository` | `HZ_TEAM__REPOSITORY` | `None` | Your team repository's URL, the invite others join with (`hz team join <url>`); none for a team repository that exists only on this machine. |
| `checkout` | `HZ_TEAM__CHECKOUT` | `None` | The local checkout of your team repository; its `workflows/` holds the `team:` contracts. |
| `layer` | `HZ_TEAM__LAYER` | `None` | Your team's layer plugin by module name, e.g. acme-payments-checkout (ADR 0040), when your team repository's hz.yaml names none or you want another; hz system layer show shows the stack it gives. |

## `[workflow]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `user_dir` | `HZ_WORKFLOW__USER_DIR` | platform-specific | Your own contracts, `<name>.prose.md`; they run on your daemon when they have a schedule. |
| `team_repository` | `HZ_WORKFLOW__TEAM_REPOSITORY` | `None` | Deprecated: `hz team join` or `hz team adopt` sets your team's checkout in `[team]` instead. A checkout of your team's state repository, used when `[team]` names none; its contracts are in `workflows/`. |
| `schedule_team` | `HZ_WORKFLOW__SCHEDULE_TEAM` | platform-specific | Team contracts your daemon runs on their schedule, by name, or `name@sha256:<hex>` to run only the content you approved (`hz workflow show` prints it); the others run only by hand, so a team's contracts don't run on every member's daemon. |
| `max_usd` | `HZ_WORKFLOW__MAX_USD` | `1.0` | Most one render may cost in US dollars, whatever a contract asks for: scheduled and manual runs, in every head. A scheduled slot's retries share it. |
| `retention_days` | `HZ_WORKFLOW__RETENTION_DAYS` | `90` | Days drafts and their `.record.json` files (the packet with the fetched inputs) are kept, counted from the render; older ones are deleted by the daemon's daily `workflow:prune` job or by `hz workflow prune`, never by reading them. 0 keeps them forever, at most 36500. Receipts are kept and hold the draft's hash, not its text (ADR 0021). |

## `[zoom]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `query` | `HZ_ZOOM__QUERY` | `'from:zoom.us'` | Mail query that finds Zoom's meeting-summary emails, in the mail capability's syntax (`from:`, `subject:`, `label:`), e.g. `from:zoom.us label:Meetings`; subjects that are no summary are skipped. Never read from a project hz.toml. |
