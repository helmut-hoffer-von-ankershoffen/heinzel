# Configuration reference

Generated from the code by `make docs`; CI fails when it is out of date. Settings come from TOML files — site, user
(`hz system config init` writes one), the nearest `hz.toml`, `$HZ_CONFIG` — and environment variables, which win.
`hz system config show` prints the values in effect; `hz system config set KEY VALUE` changes them.
Plugins add their own tables.

## `[hz]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `data_dir` | `HZ_DATA_DIR` | platform-specific | Where Heinzelmännchen keeps its data on this computer. |
| `cache_dir` | `HZ_CACHE_DIR` | platform-specific | Where Heinzelmännchen keeps caches; anything in it can be deleted at any time. |
| `state_dir` | `HZ_STATE_DIR` | platform-specific | Where Heinzelmännchen keeps runtime state, such as locks and whether the daemon is paused. |
| `log_dir` | `HZ_LOG_DIR` | platform-specific | Where background work, such as the daemon, writes its logs. |
| `log_level` | `HZ_LOG_LEVEL` | `'WARNING'` | How much Heinzelmännchen and its modules write to the log. Choices: DEBUG, INFO, WARNING, ERROR, CRITICAL. |
| `output_format` | `HZ_OUTPUT_FORMAT` | `'text'` | What every command prints unless it is asked for another format: text for people, or JSON for scripts and agents. Choices: text, json. |
| `expert` | `HZ_EXPERT` | `False` | List every command in help, shell completion and the web app, not just the everyday ones. Hidden commands still work, and MCP and REST always list everything. Turn it on or off with hz system expert; a project's settings can't. |
| `playful` | `HZ_PLAYFUL` | `True` | A small Heinzelmännchen in the terminal: in the start screen, the setup, the spinner of long work and the done and error lines. Only on a colour terminal, never in JSON, a pipe or CI. |
| `plugins_enabled` | `HZ_PLUGINS_ENABLED` | `True` | Load the modules that installed plugins bring. Turn it off only to find a broken plugin. |
| `update_check` | `HZ_UPDATE_CHECK` | `True` | Look for a newer release at most once an hour and mention it after a command's output. The check sends nothing about you, and never runs in CI or in JSON, MCP or REST output. Never read from a project's hz.toml. |
| `update_channel` | `HZ_UPDATE_CHANNEL` | `'release'` | What updating moves to: the newest release, or the newest commit of the main branch. An installation on main moves back to releases once one is as new as it. Choices: release, main. |
| `disabled_modules` | `HZ_DISABLED_MODULES` | `frozenset()` | Modules Heinzelmännchen doesn't load, with their submodules. In the environment, separate them with commas. |
| `ui_port` | `HZ_UI_PORT` | `8737` | The port the web app is served on, reachable from this computer only. It stays the same so an installed app keeps its address. Never read from a project's hz.toml. |
| `api_allowed_hosts` | `HZ_API_ALLOWED_HOSTS` | `frozenset()` | Host names the REST API answers besides this computer's own, for when it listens on another network interface. Requests naming any other host are refused. In the environment, separate them with commas. Never read from a project's hz.toml. |

## `[capabilities]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `agent_runner` | `HZ_CAPABILITIES__AGENT_RUNNER` | `'claude-code'` | What runs drafts and chats: Claude Code (the default), your own command, or a runner a plugin brings. Never read from a project's hz.toml. |
| `ai_access` | `HZ_CAPABILITIES__AI_ACCESS` | `'claude-code'` | How Heinzelmännchen reaches AI: Claude Code (the default), your company's AI gateway, Pydantic AI, or a way a plugin brings. Never read from a project's hz.toml. |
| `calendar` | `HZ_CAPABILITIES__CALENDAR` | `None` | The connector that reads your calendar, such as Google. Never read from a project's hz.toml. |
| `chat` | `HZ_CAPABILITIES__CHAT` | `None` | The connector for team chat, such as Slack. Never read from a project's hz.toml. |
| `code_host` | `HZ_CAPABILITIES__CODE_HOST` | `None` | The connector for pull requests and code, such as GitHub. Never read from a project's hz.toml. |
| `directory` | `HZ_CAPABILITIES__DIRECTORY` | `'one-on-ones'` | Who reports to whom: guessed from recurring one-on-ones in your calendar (the default, each guess for you to confirm), or read from a directory a plugin connects. Never read from a project's hz.toml. |
| `documents` | `HZ_CAPABILITIES__DOCUMENTS` | `None` | The connector for documents and pages, such as Confluence. Never read from a project's hz.toml. |
| `mail` | `HZ_CAPABILITIES__MAIL` | `None` | The connector that reads your mail, such as Google. Never read from a project's hz.toml. |
| `meetings` | `HZ_CAPABILITIES__MEETINGS` | `None` | The connector for meeting summaries, such as Zoom. Never read from a project's hz.toml. |
| `tracker` | `HZ_CAPABILITIES__TRACKER` | `None` | The connector for issues, such as GitHub or Jira. Never read from a project's hz.toml. |
| `team_graph` | `HZ_CAPABILITIES__TEAM_GRAPH` | `'hz-graph'` | What draws your team's graph: the built-in graph of your team's contracts and telemetry (the default), or one a plugin brings. Never read from a project's hz.toml. |

## `[ai]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `daily_budget_usd` | `HZ_AI__DAILY_BUDGET_USD` | `5.0` | What you mean to spend on AI a day, in US dollars: the footer's meter fills against it and turns amber at 75% and red at 100%. Nothing is stopped by it; each run keeps its own cap. Never read from a project's hz.toml. |

## `[ai.access]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `gateway_title` | `HZ_AI_ACCESS__GATEWAY_TITLE` | `'Company AI gateway'` | What people pick your company's AI gateway by. Example: "Example AI". Never read from a project's hz.toml. |
| `gateway_summary` | `HZ_AI_ACCESS__GATEWAY_SUMMARY` | `"Your company's AI gateway CLI, set up in [ai.access]."` | One sentence on how the gateway reaches AI. Never read from a project's hz.toml. |
| `gateway_status_command` | `HZ_AI_ACCESS__GATEWAY_STATUS_COMMAND` | platform-specific | The command, one argument per item, that says whether you are signed in without calling a model: exit code 0 when signed in, optionally with a JSON object on its output naming signed_in, models and detail. The gateway isn't set up until this is. Example: ["example", "ai", "status", "--json"]. Never read from a project's hz.toml. |
| `gateway_test_command` | `HZ_AI_ACCESS__GATEWAY_TEST_COMMAND` | platform-specific | One cheap live call, one argument per item, run only when you test the gateway: exit code 0 when it answered, optionally with a JSON object naming ok, detail, model and cost_usd. Example: ["example", "ai", "ping"]. Never read from a project's hz.toml. |
| `gateway_sign_in` | `HZ_AI_ACCESS__GATEWAY_SIGN_IN` | `''` | The command that signs you in, shown as the fix when you aren't. Example: "example login". Never read from a project's hz.toml. |
| `gateway_runner` | `HZ_AI_ACCESS__GATEWAY_RUNNER` | `None` | The agent runner that serves the gateway's chat and coding, such as your own command runner set to the gateway's agent CLI. Choosing the gateway as your AI access binds it. Never read from a project's hz.toml. |
| `gateway_hosts` | `HZ_AI_ACCESS__GATEWAY_HOSTS` | platform-specific | The hosts the gateway sends prompts to, as host names or addresses; shown wherever AI access is listed. Example: "ai.example.com". Never read from a project's hz.toml. |
| `gateway_models` | `HZ_AI_ACCESS__GATEWAY_MODELS` | platform-specific | The models the gateway offers, when its status command doesn't say. Never read from a project's hz.toml. |
| `gateway_env` | `HZ_AI_ACCESS__GATEWAY_ENV` | platform-specific | Environment variables the gateway's commands get besides PATH, HOME, LANG, LC_* and TMPDIR. Heinzelmännchen's credential variables are never passed. Never read from a project's hz.toml. |
| `chatgpt_model` | `HZ_AI_ACCESS__CHATGPT_MODEL` | `None` | The model used with your ChatGPT plan, one it offers through Sign in with ChatGPT; sign in first. Never read from a project's hz.toml. |
| `model` | `HZ_AI_ACCESS__MODEL` | `None` | The model Pydantic AI uses: vendor:model for a vendor's API, or the name your endpoint knows when a base address is set. Its key is the credential ai.access/api-key or the vendor's own variable. Example: "anthropic:claude-sonnet-4-5". Never read from a project's hz.toml. |
| `base_url` | `HZ_AI_ACCESS__BASE_URL` | `None` | The address of an OpenAI-compatible endpoint, such as Ollama, LM Studio, vLLM or OpenRouter. A key is optional and goes only over https, or over http to this computer. Example: "http://localhost:11434/v1". Never read from a project's hz.toml. |

## `[atlassian]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `site_url` | `HZ_ATLASSIAN__SITE_URL` | `None` | The Atlassian Cloud site you are signed in to with the Atlassian CLI; links to issues and pages are built on it. Only your own settings can set it, never a project's. Example: "https://example.atlassian.net". |
| `acli` | `HZ_ATLASSIAN__ACLI` | `'acli'` | The Atlassian CLI to run: a name found on your PATH, or the full path to it. Never read from a project's settings. |
| `timeout` | `HZ_ATLASSIAN__TIMEOUT` | `60.0` | How long to wait for each Atlassian CLI command before stopping it. Never read from a project's hz.toml. |

## `[clock]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `zones` | `HZ_CLOCK__ZONES` | `[{'name': 'Europe/Berlin', 'hours': [9, 18]}, {'name': 'America/New_York', 'hours': [9, 18]}, {'name': 'America/Los_Angeles', 'hours': [9, 18]}]` | The time zones your team works in, with the working hours in each. The first one is yours. Example: [{ hours = [10, 19], name = "Asia/Tokyo" }]. |
| `working_hours` | `HZ_CLOCK__WORKING_HOURS` | `None` | Deprecated: the hours of every zone given by name only. Give each zone its hours instead. |

## `[github]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `host` | `HZ_GITHUB__HOST` | `'github.com'` | The GitHub host to use: github.com, or your GitHub Enterprise Server's host name. The GitHub CLI's sign-in for that host is used. Only your own settings can set it, never a project's. Example: "github.com". |
| `gh` | `HZ_GITHUB__GH` | `'gh'` | The GitHub CLI to run: a name found on your PATH, or the full path to it. Never read from a project's hz.toml. |
| `timeout` | `HZ_GITHUB__TIMEOUT` | `60.0` | How long to wait for each GitHub CLI call before stopping it. Never read from a project's hz.toml. |

## `[google]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `gws_path` | `HZ_GOOGLE__GWS_PATH` | `None` | The full path to the Google Workspace CLI; the one on your PATH when not set. Only your own settings can set it, never a project's. |
| `timeout` | `HZ_GOOGLE__TIMEOUT` | `60.0` | How long to wait for each Google Workspace CLI run before stopping it. Never read from a project's hz.toml. |

## `[graph]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `time_zone` | `HZ_GRAPH__TIME_ZONE` | `None` | The time zone your team's contract schedules are read in; your computer's when not set. |
| `offline_after` | `HZ_GRAPH__OFFLINE_AFTER` | `'2d'` | How long a Heinzelmännchen may stay silent, without even a heartbeat, before it shows as offline: a number with m, h or d, such as 36h. |
| `weeks` | `HZ_GRAPH__WEEKS` | `4` | How many weeks of kept promises the team graph counts. |

## `[heinzel]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `user_dir` | `HZ_HEINZEL__USER_DIR` | platform-specific | The folder of your own contracts; those with a schedule run on your daemon. Never read from a project's hz.toml. |
| `team_repository` | `HZ_HEINZEL__TEAM_REPOSITORY` | `None` | Deprecated: `hz team join` or `hz team adopt` sets your team's checkout in `[team]` instead. A checkout of your team's state repository, used when `[team]` names none; its contracts are in `workflows/`. Never read from a project's hz.toml. |
| `schedule_team` | `HZ_HEINZEL__SCHEDULE_TEAM` | platform-specific | Team contracts your daemon runs on their schedule, by name; add @sha256: and a hash to run only the version you approved. The others run only when started by hand, so a team's contracts don't run on everyone's daemon. Never read from a project's hz.toml. |
| `max_usd` | `HZ_HEINZEL__MAX_USD` | `1.0` | The most one run of a contract may cost in US dollars, scheduled or by hand, whatever the contract asks for. A scheduled run's retries share it. Never read from a project's hz.toml. |
| `session_max_usd` | `HZ_HEINZEL__SESSION_MAX_USD` | `2.0` | The most one durable Heinzel conversation may cost in US dollars, across its model turns. Never read from a project's hz.toml. |
| `message_max_usd` | `HZ_HEINZEL__MESSAGE_MAX_USD` | `0.2` | The most one model turn in a durable Heinzel conversation may cost in US dollars. Never read from a project's hz.toml. |
| `retention_days` | `HZ_HEINZEL__RETENTION_DAYS` | `90` | How long drafts and the inputs they were made from are kept, counted from when they were made; 0 keeps them forever. Receipts are always kept, with a hash of the draft instead of its text. Never read from a project's hz.toml. |

## `[org]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `handle` | `HZ_ORG__HANDLE` | `None` | Your short name in teams, pull requests and contracts: lowercase letters, digits and dashes. Your GitHub login is a good choice. Example: "mira". Never read from a project's hz.toml. |
| `role` | `HZ_ORG__ROLE` | `None` | What you do on your team. Choices: engineer, principal, engineering-manager, engineering-leadership, product-manager, product-leadership, program-manager. Never read from a project's hz.toml. |
| `timezone` | `HZ_ORG__TIMEZONE` | `None` | Your time zone. Never read from a project's hz.toml. |
| `working_hours` | `HZ_ORG__WORKING_HOURS` | `(9, 18)` | When your working day starts and ends. In the environment, write them as 09-18. Never read from a project's hz.toml. |
| `github` | `HZ_ORG__GITHUB` | `None` | Your GitHub login, needed to approve and own contracts in a team. Example: "mira". Never read from a project's hz.toml. |
| `coding_agent` | `HZ_ORG__CODING_AGENT` | `None` | The coding agent you write drafts with. Choices: claude-code, command, none. Never read from a project's hz.toml. |
| `name` | `HZ_ORG__NAME` | `None` | Your name, from your Google or GitHub sign-in. It stays on this computer, never in a repository. Never read from a project's hz.toml. |
| `email` | `HZ_ORG__EMAIL` | `None` | Your work email, from your Google sign-in. It stays on this computer, never in a repository. Example: "mira@example.com". Never read from a project's hz.toml. |

## `[report]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `user` | `HZ_REPORT__USER` | `None` | Your handle in your team's roster; every entry you report carries it. Example: "mira". Never read from a project's hz.toml. |

## `[system.agent]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `skill_dirs` | `HZ_SYSTEM_AGENT__SKILL_DIRS` | platform-specific | Folders Heinzelmännchen's skills are copied into when you install them for your coding agents, one per agent. |

## `[system.changelog]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `github_token` | `HZ_SYSTEM_CHANGELOG__GITHUB_TOKEN` | `None` | A GitHub token for reading what newer releases bring, which works without one at a lower rate limit. Also read from GH_TOKEN or GITHUB_TOKEN, sent to GitHub only, and never by the hourly update check. |

## `[system.credential]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `backend` | `HZ_SYSTEM_CREDENTIAL__BACKEND` | `'auto'` | Where Heinzelmännchen keeps passwords and tokens: the system keychain when there is one, any configured keyring, or read-only environment variables named HZ_CREDENTIAL_<SERVICE>_<NAME>. Choices: auto, keyring, environment. |

## `[system.daemon]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `unit_dir` | `HZ_SYSTEM_DAEMON__UNIT_DIR` | platform-specific | Where installing the daemon writes its LaunchAgent on macOS, or its systemd user units on Linux. |

## `[system.directory]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `lookback_days` | `HZ_SYSTEM_DIRECTORY__LOOKBACK_DAYS` | `56` | How many days of your calendar, up to today, to search for recurring one-on-ones; at least 42, so a meeting every two weeks shows up three times. |
| `min_occurrences` | `HZ_SYSTEM_DIRECTORY__MIN_OCCURRENCES` | `3` | How often two people must have met alone, weekly or every two weeks, before a reporting line between them is guessed. |

## `[system.notify]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `enabled` | `HZ_SYSTEM_NOTIFY__ENABLED` | `True` | Keep a list of notices at all; off turns every kind off. Never read from a project's hz.toml. |
| `draft` | `HZ_SYSTEM_NOTIFY__DRAFT` | `True` | Notify when a draft is ready to read and approve. Never read from a project's hz.toml. |
| `failed` | `HZ_SYSTEM_NOTIFY__FAILED` | `True` | Notify when a contract run on your daemon failed. Never read from a project's hz.toml. |
| `doctor` | `HZ_SYSTEM_NOTIFY__DOCTOR` | `True` | Notify when a health check finds something degraded or down. Never read from a project's hz.toml. |
| `release` | `HZ_SYSTEM_NOTIFY__RELEASE` | `False` | Notify when a newer release of Heinzelmännchen is out. Never read from a project's hz.toml. |
| `quiet` | `HZ_SYSTEM_NOTIFY__QUIET` | `False` | Keep notices in the list but never pop one up, not even an urgent one. Never read from a project's hz.toml. |
| `working_hours` | `HZ_SYSTEM_NOTIFY__WORKING_HOURS` | `True` | Hold notices that aren't urgent until your working hours start, Monday to Friday, in your time zone (both under Your profile). Never read from a project's hz.toml. |
| `per_hour` | `HZ_SYSTEM_NOTIFY__PER_HOUR` | `6` | At most this many pop-ups an hour; the rest go to the list only. Never read from a project's hz.toml. |
| `repeat_hours` | `HZ_SYSTEM_NOTIFY__REPEAT_HOURS` | `24` | The same notice again within this many hours is dropped. Never read from a project's hz.toml. |
| `native` | `HZ_SYSTEM_NOTIFY__NATIVE` | `True` | Let the daemon show system notifications (Heinzel Notifier on macOS, notify-send on Linux) when no web app is open. Never read from a project's hz.toml. |

## `[system.plugin]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `github_token` | `HZ_SYSTEM_PLUGIN__GITHUB_TOKEN` | `None` | A GitHub token for searching plugins and reading a repository before adding a plugin from it, when the GitHub CLI isn't installed; both work without one at a lower rate limit. Also read from GH_TOKEN or GITHUB_TOKEN, and sent to GitHub only. |

## `[system.runner]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `model` | `HZ_SYSTEM_RUNNER__MODEL` | `None` | The model every run uses; the agent's own default when not set. Example: "sonnet". |
| `claude_executable` | `HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE` | `'claude'` | The Claude Code CLI to run: a name found on your PATH, or the full path to it. Never read from a project's hz.toml. |
| `codex_executable` | `HZ_SYSTEM_RUNNER__CODEX_EXECUTABLE` | `'codex'` | The Codex CLI to run: a name found on your PATH, or the full path to it. Never read from a project's hz.toml. |
| `codex_model` | `HZ_SYSTEM_RUNNER__CODEX_MODEL` | `None` | The model every Codex run uses; Codex's own default when not set. Example: "gpt-5-codex". |
| `codex_effort` | `HZ_SYSTEM_RUNNER__CODEX_EFFORT` | `None` | How hard every Codex run thinks; Codex's own default when not set. Choices: minimal, low, medium, high, xhigh. |
| `read_only_tools` | `HZ_SYSTEM_RUNNER__READ_ONLY_TOOLS` | `['Read', 'Grep', 'Glob']` | Claude Code tools a run may use; a profile asking for any other is refused. Add only tools that can't change anything. Never read from a project's hz.toml. |
| `command` | `HZ_SYSTEM_RUNNER__COMMAND` | platform-specific | Your own runner's command line, one argument per item, never run through a shell. Placeholders: {prompt_file}, {contract_file}, {output_file}, {working_dir}, {tools}, {model}, {timeout_seconds}, {max_usd} and {mcp_config}, a JSON file of MCP servers for a chat's tools. Double braces are literal braces. Example: ["mytool", "ai", "run", "--prompt", "{prompt_file}"]. Never read from a project's hz.toml. |
| `command_output` | `HZ_SYSTEM_RUNNER__COMMAND_OUTPUT` | `'stdout'` | Where your own runner's result is read from: what the command prints, or the output file. Choices: stdout, file. Never read from a project's hz.toml. |
| `command_format` | `HZ_SYSTEM_RUNNER__COMMAND_FORMAT` | `'text'` | How your own runner's result is read: as the draft itself, or as JSON with the draft under the draft key and, optionally, usage and total_cost_usd. Choices: text, json. Never read from a project's hz.toml. |
| `command_draft_key` | `HZ_SYSTEM_RUNNER__COMMAND_DRAFT_KEY` | `'result'` | The key of the draft in your own runner's JSON result. Never read from a project's hz.toml. |
| `command_enforces_read_only` | `HZ_SYSTEM_RUNNER__COMMAND_ENFORCES_READ_ONLY` | `False` | Your own runner refuses to run until this is on. Turn it on once your command limits its agent to tools that can't change anything. Never read from a project's hz.toml. |
| `command_read_only_tools` | `HZ_SYSTEM_RUNNER__COMMAND_READ_ONLY_TOOLS` | platform-specific | Tools, as your command names them, that a profile may ask your own runner for; any other is refused. Never read from a project's hz.toml. |
| `command_env` | `HZ_SYSTEM_RUNNER__COMMAND_ENV` | platform-specific | Environment variables your command gets besides PATH, HOME, LANG, LC_* and TMPDIR, such as the one holding its own sign-in. Heinzelmännchen's credential variables are never passed. Never read from a project's hz.toml. |
| `ask_max_usd` | `HZ_SYSTEM_RUNNER__ASK_MAX_USD` | `0.25` | The most one answer, or one chat message, may cost in US dollars. Never read from a project's hz.toml. |

## `[system.runner.chat]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `session_max_usd` | `HZ_SYSTEM_RUNNER_CHAT__SESSION_MAX_USD` | `2.0` | The most one chat may cost in US dollars, all its messages together. One message never costs more than the most per answer. Never read from a project's hz.toml. |
| `keep_days` | `HZ_SYSTEM_RUNNER_CHAT__KEEP_DAYS` | `30` | How long a chat is kept after its last message, then removed. |
| `keep_sessions` | `HZ_SYSTEM_RUNNER_CHAT__KEEP_SESSIONS` | `50` | How many chats are kept at most; the oldest beyond that are removed. |
| `tools` | `HZ_SYSTEM_RUNNER_CHAT__TOOLS` | `'everyday'` | Which operations the chat's agent can use: the everyday ones, or all of them. Every tool is sent with every message, so fewer make each message cheaper. Choices: everyday, all. Never read from a project's hz.toml. |

## `[system.skill]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `chat` | `HZ_SYSTEM_SKILL__CHAT` | `None` | The skills the chat may load, by name; when not set, the core's own skills. Never read from a project's hz.toml. |
| `personal_dir` | `HZ_SYSTEM_SKILL__PERSONAL_DIR` | `None` | A folder of your own skills, each a folder with a SKILL.md; they win over every plugin's. Defaults to a skills folder in your configuration folder. Never read from a project's hz.toml. |
| `export_dirs` | `HZ_SYSTEM_SKILL__EXPORT_DIRS` | platform-specific | The folders chosen skills are exported into, one per coding agent, each named after the skill with heinzel- before it. Never read from a project's hz.toml. |

## `[system.sound]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `name` | `HZ_SYSTEM_SOUND__NAME` | `'off'` | The soundscape of the web app; off is no sound, and plugins can bring others. Never read from a project's hz.toml. |
| `volume` | `HZ_SYSTEM_SOUND__VOLUME` | `50` | How loud, from 0 to 100. Never read from a project's hz.toml. |
| `follow_theme` | `HZ_SYSTEM_SOUND__FOLLOW_THEME` | `False` | Play the soundscape that goes with your theme, when it has one, instead of the one you chose. Never read from a project's hz.toml. |
| `mute_in_meetings` | `HZ_SYSTEM_SOUND__MUTE_IN_MEETINGS` | `True` | Mute while your calendar says you are in a meeting; needs a connected calendar. Never read from a project's hz.toml. |
| `duck_while_typing` | `HZ_SYSTEM_SOUND__DUCK_WHILE_TYPING` | `True` | Play softer while you type in the chat. Never read from a project's hz.toml. |

## `[system.state]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `personal` | `HZ_SYSTEM_STATE__PERSONAL` | `None` | Your own private state repository, which hz pushes this machine's presence and adoption to, on the branch hz/<your handle>/<machine>; e.g. https://github.com/mira-example/heinzel-state-mira. Unset, nothing leaves this machine. Never read from a project's hz.toml. |

## `[system.theme]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `name` | `HZ_SYSTEM_THEME__NAME` | `'default'` | The theme of the web app; default is the built-in one, and plugins can bring others. Never read from a project's hz.toml. |

## `[system.ui]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `contributions` | `HZ_SYSTEM_UI__CONTRIBUTIONS` | platform-specific | Which status items, actions and widgets show in which place of the app; anything you leave out keeps the place its module chose. Never read from a project's hz.toml. |

## `[team]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `repository` | `HZ_TEAM__REPOSITORY` | `None` | Your team repository's address, the invite others join with; empty for a team repository that exists only on this computer. Example: "https://github.com/example/team.git". Never read from a project's hz.toml. |
| `checkout` | `HZ_TEAM__CHECKOUT` | `None` | The folder your team repository is checked out in; its workflows folder holds the team's contracts. Never read from a project's hz.toml. |
| `layer` | `HZ_TEAM__LAYER` | `None` | The layer plugin your team uses, by module name, when your team repository names none or you want another. Never read from a project's hz.toml. |

## `[zoom]`

| Key | Environment variable | Default | Description |
| --- | --- | --- | --- |
| `query` | `HZ_ZOOM__QUERY` | `'from:zoom.us'` | The mail search that finds Zoom's meeting-summary emails, in your mail's search syntax; mails that aren't summaries are skipped. Never read from a project's settings. Example: "from:zoom.us label:Meetings". |
