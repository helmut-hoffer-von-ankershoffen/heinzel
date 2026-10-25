# Modules

Generated from the code by `make docs`; CI fails when it is out of date. Every operation is the same in every head:
a `hz` command, an MCP tool (`hz system serve mcp`), a REST endpoint (`hz system serve api`) and an endpoint of the web
app's API (`hz ui`, under `/api`, behind its session token). Plugins add their own modules; `hz system modules` lists
what is installed. Operations marked *expert* are listed by `hz --help`, shell completion and the web app only in expert
mode (`hz system expert on`); every one runs either way, and MCP and REST list them all (ADR 0041).

| Module | What it does |
| --- | --- |
| [`ai`](#ai) | How hz reaches AI, and what it spends: tokens and cost per run. |
| [`ai.access`](#aiaccess) | Pick how hz reaches AI: Claude Code, your company's gateway, or an option a plugin adds. |
| [`atlassian`](#atlassian) | Read Jira issues and Confluence pages through acli: the tracker and documents capabilities. |
| [`clock`](#clock) | What time it is for everyone on the team. |
| [`github`](#github) | Read pull requests and issues from GitHub: the code-host and tracker capabilities. |
| [`google`](#google) | Read Google Docs, Sheets, Drive, Calendar and Gmail: the documents, calendar and mail capabilities. |
| [`graph`](#graph) | Are your team's promises kept? Artifacts and Heinzelmännchen, and where the flow waits. |
| [`heinzel`](#heinzel) | Your Heinzelmännchen: durable agents that keep your team's documents true. |
| [`org`](#org) | Your profile, and the team, org and company you work with. |
| [`report`](#report) | Telemetry: log what a Heinzelmännchen did, for your team's graph (`hz report published <id> <url>`). |
| [`system`](#system) | Inspect, configure, diagnose and update Heinzelmännchen itself. |
| [`system.agent`](#systemagent) | Install the Heinzelmännchen skills for coding agents. |
| [`system.capability`](#systemcapability) | Show which adapters serve which capabilities, and who reads and writes what. |
| [`system.changelog`](#systemchangelog) | Release notes of the installed version and of newer releases. |
| [`system.claude`](#systemclaude) | See the MCP servers and skills you already set up for Claude, without using any of them. |
| [`system.config`](#systemconfig) | Show, get, set and initialize configuration. |
| [`system.connector`](#systemconnector) | Show whether each connector is signed in and serves something, and test one live. |
| [`system.credential`](#systemcredential) | Store and check the credentials modules need, in the OS keychain. |
| [`system.daemon`](#systemdaemon) | Run modules' jobs in the background, supervised by launchd or systemd. |
| [`system.directory`](#systemdirectory) | Guess reporting lines from recurring 1:1s in your calendar, for setup to confirm. |
| [`system.doctor`](#systemdoctor) | Check that Heinzelmännchen and its modules are healthy. |
| [`system.expert`](#systemexpert) | Expert mode: list every command, or just the everyday ones. |
| [`system.layer`](#systemlayer) | Show the layer plugins installed and the stack you are in, general to specific. |
| [`system.notify`](#systemnotify) | Pop up what deserves your attention: in the web app while it is open, else natively. |
| [`system.plugin`](#systemplugin) | Find, add, remove, enable, disable, list and scaffold plugins. |
| [`system.runner`](#systemrunner) | Run bounded agent sessions with Claude Code or a configured command. |
| [`system.runner.chat`](#systemrunnerchat) | Chat with the agent runner about where you are; it acts through hz once you confirm. |
| [`system.theme`](#systemtheme) | Pick how the web app looks: the built-in theme or one a plugin offers. |
| [`system.ui`](#systemui) | List what modules add to the web app: status items, header actions and Today widgets. |
| [`team`](#team) | Your team's repository: create one (`hz team new`), join one by its URL (`hz team join`), add people to it. |
| [`zoom`](#zoom) | Read meetings from Zoom's meeting-summary emails: the meetings capability. |

## `ai`

How hz reaches AI, and what it spends: tokens and cost per run.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show the tokens and cost of AI runs today and this week, by access, model, Heinzelmännchen or cause. | `hz ai usage` | `ai_usage` | — | `GET /api/ai/usage` |  | expert |

Settings: [`[ai]`](CONFIGURATION.md#ai).

## `ai.access`

Pick how hz reaches AI: Claude Code, your company's gateway, or an option a plugin adds.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List the AI access options, the one in use, whether each is signed in, and what runs each use. | `hz ai access list` | `ai_access_list` | — | `GET /api/ai/access/list` |  | expert |
| Use an AI access option: sets capabilities.ai_access, and agent_runner if the option names one. | `hz ai access use` | — | — | `POST /api/ai/access/use` | yes | expert |
| Run one cheap live check of an AI access option and keep the result; this may cost a little. | `hz ai access test` | — | — | `POST /api/ai/access/test` | yes | expert |

Settings: [`[ai.access]`](CONFIGURATION.md#aiaccess).

Health checks: reported by `hz system doctor`.

## `atlassian`

Read Jira issues and Confluence pages through acli: the tracker and documents capabilities.

First-party plugin `heinzel-atlassian`: `hz system plugin add atlassian --confirm`.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show this plugin's version, the Atlassian site and the acli commands it runs. | `hz atlassian info` | `atlassian_info` | `GET /atlassian/info` | `GET /api/atlassian/info` |  | expert |

Settings: [`[atlassian]`](CONFIGURATION.md#atlassian).

Health checks: reported by `hz system doctor`.

## `clock`

What time it is for everyone on the team.

First-party plugin `heinzel-clock`: `hz system plugin add clock --confirm`.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show the time in each of the team's time zones. | `hz clock` | `clock` | `GET /clock` | `GET /api/clock` |  |  |

Settings: [`[clock]`](CONFIGURATION.md#clock).

Health checks: reported by `hz system doctor`.

Setup: `hz setup clock` (optional), also part of `hz setup`.

## `github`

Read pull requests and issues from GitHub: the code-host and tracker capabilities.

First-party plugin `heinzel-github`: `hz system plugin add github --confirm`.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show this plugin's version, the Heinzelmännchen it runs on and the GitHub host it reads. | `hz github info` | `github_info` | `GET /github/info` | `GET /api/github/info` |  | expert |

Settings: [`[github]`](CONFIGURATION.md#github).

Health checks: reported by `hz system doctor`.

## `google`

Read Google Docs, Sheets, Drive, Calendar and Gmail: the documents, calendar and mail capabilities.

First-party plugin `heinzel-google`: `hz system plugin add google --confirm`.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show this plugin's version, the Heinzelmännchen it runs on and the gws, scopes and APIs it uses. | `hz google info` | `google_info` | `GET /google/info` | `GET /api/google/info` |  | expert |
| List the Google Docs and Sheets you can open that changed recently, newest first. | `hz google files` | `google_files` | `GET /google/files` | `GET /api/google/files` |  | expert |

Settings: [`[google]`](CONFIGURATION.md#google).

Health checks: reported by `hz system doctor`.

Setup: `hz setup google` (optional), also part of `hz setup`.

## `graph`

Are your team's promises kept? Artifacts and Heinzelmännchen, and where the flow waits.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show your team's promises: who keeps which artifact, how each promise stands, where the flow waits. | `hz graph` | `graph` | — | `GET /api/graph` |  |  |

Settings: [`[graph]`](CONFIGURATION.md#graph).

## `heinzel`

Your Heinzelmännchen: durable agents that keep your team's documents true.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List your Heinzelmännchen, one per contract: owner, schedule, state, last and next run. | `hz heinzel list` | `heinzel_list` | — | `GET /api/heinzel/list` |  |  |
| Write a starter contract, or a playbook's template (--template), into your workflows directory. | `hz heinzel new` | `heinzel_new` | — | — | yes |  |
| List the playbooks plugins contribute: asset types, template contracts and recommended settings. | `hz heinzel playbooks` | `heinzel_playbooks` | — | `GET /api/heinzel/playbooks` |  | expert |
| Find candidates for each playbook's asset types through the capabilities you have bound; reads only. | `hz heinzel discover` | `heinzel_discover` | — | `GET /api/heinzel/discover` |  | expert |
| Show a contract as the daemon runs it: schedule, requirements, adapters, readiness, next steps. | `hz heinzel show` | `heinzel_show` | — | `GET /api/heinzel/show` |  |  |
| List the drafts runs saved locally, newest first, with their paths and records, or show the newest. | `hz heinzel drafts` | `heinzel_drafts` | — | — |  |  |
| Record how much of a draft you changed before sending it; `hz heinzel show` reports it. | `hz heinzel sent` | `heinzel_sent` | — | — | yes | expert |
| Show the workflow your contracts make: what each maintains and reads, cycles, unmaintained inputs. | `hz heinzel graph` | `heinzel_graph` | — | `GET /api/heinzel/graph` |  | expert |
| Check contracts and list every problem with file and line; exits with 1 on errors, so CI can gate. | `hz heinzel check` | `heinzel_check` | — | `GET /api/heinzel/check` |  | expert |
| Wake a Heinzelmännchen now: fetch its inputs, run the agent, save the draft; nothing is published. | `hz heinzel wake` | `heinzel_wake` | — | — | yes |  |
| Delete drafts and records older than `[heinzel] retention_days` (default 90); receipts are kept. | `hz heinzel prune` | `heinzel_prune` | — | — | yes | expert |

Settings: [`[heinzel]`](CONFIGURATION.md#heinzel).

Health checks: reported by `hz system doctor`.

## `org`

Your profile, and the team, org and company you work with.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show your profile: who you are, your role, time zone and working hours, and what is still missing. | `hz org show` | `org_show` | — | `GET /api/org/show` |  |  |
| Show the meeting you are in now and your next one, from your calendar, or how to connect one. | `hz org agenda` | `org_agenda` | — | `GET /api/org/agenda` |  | expert |
| Show what your team recommends beside what you have; apply only what you accept, to your user file. | `hz org sync` | `org_sync` | — | — | yes |  |

Settings: [`[org]`](CONFIGURATION.md#org).

Health checks: reported by `hz system doctor`.

Setup: `hz setup org` (required), also part of `hz setup`.

## `report`

Telemetry: log what a Heinzelmännchen did, for your team's graph (`hz report published <id> <url>`).

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Log that a Heinzelmännchen read an artifact, which version, and by when its contract needs it. | `hz report read` | `report_read` | — | — | yes |  |
| Log that a Heinzelmännchen put a draft where it will be approved, and from which contract version. | `hz report drafted` | `report_drafted` | — | — | yes |  |
| Log that a Heinzelmännchen's draft was approved, by whom; the entry's time is when. | `hz report approved` | `report_approved` | — | — | yes |  |
| Log where a Heinzelmännchen published: a document, an email address, a chat channel. | `hz report published` | `report_published` | — | — | yes |  |
| Log that a Heinzelmännchen used a stale version of an input to keep its deadline. | `hz report fell-back` | `report_fell_back` | — | — | yes |  |
| Log that a Heinzelmännchen is alive; one a day is enough for the team to see it isn't offline. | `hz report heartbeat` | `report_heartbeat` | — | — | yes |  |
| Copy your Heinzelmännchen's logs into your team repository's checkout, for you to commit and push. | `hz report sync` | `report_sync` | — | — | yes | expert |

Settings: [`[report]`](CONFIGURATION.md#report).

## `system`

Inspect, configure, diagnose and update Heinzelmännchen itself.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show versions, interpreter, settings, modules and what modules report. | `hz system info` | `system_info` | `GET /system/info` | `GET /api/system/info` |  | expert |
| List every module and the lets it advertises. | `hz system modules` | `system_modules` | `GET /system/modules` | `GET /api/system/modules` |  | expert |
| Ask where Heinzelmännchen was installed from for its latest release, and whether to update. | `hz system release` | `system_release` | `GET /system/release` | `GET /api/system/release` |  | expert |
| Update Heinzelmännchen, keeping plugins; see what it brings with `hz system changelog --newer`. | `hz system update` | `system_update` | `POST /system/update` | `POST /api/system/update` | yes |  |

## `system.agent`

Install the Heinzelmännchen skills for coding agents.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List the Agent Skills that ship with Heinzelmännchen. | `hz system agent skills` | `system_agent_skills` | `GET /system/agent/skills` | `GET /api/system/agent/skills` |  | expert |
| Copy Heinzelmännchen's skills into each configured agent skill directory. | `hz system agent install` | `system_agent_install` | `POST /system/agent/install` | `POST /api/system/agent/install` | yes | expert |
| Remove Heinzelmännchen's skills from each configured agent skill directory. | `hz system agent uninstall` | `system_agent_uninstall` | `POST /system/agent/uninstall` | `POST /api/system/agent/uninstall` | yes | expert |

Settings: [`[system.agent]`](CONFIGURATION.md#systemagent).

## `system.capability`

Show which adapters serve which capabilities, and who reads and writes what.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List capabilities, their bindings and adapters, and every requirement with its adapter and hosts. | `hz system capability list` | `system_capability_list` | `GET /system/capability/list` | `GET /api/system/capability/list` |  | expert |

Health checks: reported by `hz system doctor`.

## `system.changelog`

Release notes of the installed version and of newer releases.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show the installed version's changelog as Markdown, or with `--newer` what newer releases bring. | `hz system changelog` | `system_changelog` | `GET /system/changelog` | `GET /api/system/changelog` |  | expert |

Settings: [`[system.changelog]`](CONFIGURATION.md#systemchangelog).

## `system.claude`

See the MCP servers and skills you already set up for Claude, without using any of them.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List the MCP servers and skills Claude Code and Claude Desktop have; nothing is switched on. | `hz system claude list` | — | — | `GET /api/system/claude/list` |  | expert |
| Count the MCP servers and skills found in Claude's configuration per source, without their names. | `hz system claude summary` | `system_claude_summary` | `GET /system/claude/summary` | `GET /api/system/claude/summary` |  | expert |

Health checks: reported by `hz system doctor`.

## `system.config`

Show, get, set and initialize configuration.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show the configuration files Heinzelmännchen reads and every setting in effect. | `hz system config show` | `system_config_show` | `GET /system/config/show` | `GET /api/system/config/show` |  | expert |
| Write a configuration file with every setting commented out, ready to edit. | `hz system config init` | `system_config_init` | `POST /system/config/init` | `POST /api/system/config/init` | yes | expert |
| Show one setting in effect, with its environment variable. | `hz system config get` | `system_config_get` | `GET /system/config/get` | `GET /api/system/config/get` |  | expert |
| Change a setting in the user or project configuration file, after validating it. | `hz system config set` | `system_config_set` | `POST /system/config/set` | `POST /api/system/config/set` | yes | expert |
| Remove a setting from the user or project configuration file, so its default applies again. | `hz system config unset` | `system_config_unset` | `POST /system/config/unset` | `POST /api/system/config/unset` | yes | expert |
| Show a configuration file in Finder, Explorer or your desktop's file manager; nothing is created. | `hz system config reveal` | — | — | `POST /api/system/config/reveal` | yes | expert |
| Show the file or folder a path setting names, such as hz.log_dir, in your file manager. | `hz system config reveal-setting` | — | — | `POST /api/system/config/reveal-setting` | yes | expert |

## `system.connector`

Show whether each connector is signed in and serves something, and test one live.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List connectors with their sign-in, what they serve and their tools; offline. | `hz system connector list` | `system_connector_list` | — | `GET /api/system/connector/list` |  | expert |
| Test a connector live: one cheap read-only call through its tool, with a short timeout. | `hz system connector test` | `system_connector_test` | — | `GET /api/system/connector/test` |  | expert |

## `system.credential`

Store and check the credentials modules need, in the OS keychain.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List the credentials modules declare and whether each is stored, never their values. | `hz system credential list` | `system_credential_list` | `GET /system/credential/list` | `GET /api/system/credential/list` |  | expert |
| Store a credential, read from standard input or a hidden prompt, never from the command line. | `hz system credential set` | — | — | — | yes | expert |
| Remove a credential's value from the keychain. | `hz system credential remove` | — | — | — | yes | expert |

Settings: [`[system.credential]`](CONFIGURATION.md#systemcredential).

Health checks: reported by `hz system doctor`.

## `system.daemon`

Run modules' jobs in the background, supervised by launchd or systemd.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Install and load the launchd agent or systemd timer that ticks every few minutes. | `hz system daemon install` | — | — | — | yes | expert |
| Unload and remove the daemon's unit; receipts and logs stay. | `hz system daemon uninstall` | — | — | — | yes | expert |
| Show whether the daemon is installed, loaded and paused, its last tick and failing jobs. | `hz system daemon status` | `system_daemon_status` | `GET /system/daemon/status` | `GET /api/system/daemon/status` |  | expert |
| Show the latest lines of the daemon's log. | `hz system daemon logs` | `system_daemon_logs` | `GET /system/daemon/logs` | `GET /api/system/daemon/logs` |  | expert |
| Run the jobs that are due and write a receipt for each; exits with 1 if any job failed. | `hz system daemon tick` | `system_daemon_tick` | — | — | yes | expert |
| Pause the daemon: ticks run no job until resumed. | `hz system daemon pause` | `system_daemon_pause` | `POST /system/daemon/pause` | `POST /api/system/daemon/pause` | yes | expert |
| Resume a paused daemon. | `hz system daemon resume` | `system_daemon_resume` | `POST /system/daemon/resume` | `POST /api/system/daemon/resume` | yes | expert |
| List every job with its schedule, next slot and last run. | `hz system daemon jobs` | `system_daemon_jobs` | `GET /system/daemon/jobs` | `GET /api/system/daemon/jobs` |  | expert |

Settings: [`[system.daemon]`](CONFIGURATION.md#systemdaemon).

Health checks: reported by `hz system doctor`.

## `system.directory`

Guess reporting lines from recurring 1:1s in your calendar, for setup to confirm.

Settings: [`[system.directory]`](CONFIGURATION.md#systemdirectory).

## `system.doctor`

Check that Heinzelmännchen and its modules are healthy.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Run every module's health checks; exits with 1 if anything is down. | `hz system doctor` | `system_doctor` | `GET /system/doctor` | `GET /api/system/doctor` |  |  |

Health checks: reported by `hz system doctor`.

## `system.expert`

Expert mode: list every command, or just the everyday ones.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show whether expert mode is on: `on` lists every command, `off` just the everyday ones. | `hz system expert` | `system_expert` | `GET /system/expert` | `GET /api/system/expert` |  |  |
| Turn expert mode on: help, completion and the web app list every command. | `hz system expert on` | `system_expert_on` | `POST /system/expert/on` | `POST /api/system/expert/on` | yes |  |
| Turn expert mode off: help, completion and the web app list the everyday commands. | `hz system expert off` | `system_expert_off` | `POST /system/expert/off` | `POST /api/system/expert/off` | yes |  |

## `system.layer`

Show the layer plugins installed and the stack you are in, general to specific.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List the installed layer plugins: where each sits, what it brings and what is wrong with the tree. | `hz system layer list` | `system_layer_list` | `GET /system/layer/list` | `GET /api/system/layer/list` |  | expert |
| Show your stack: company, org and site, team; what the layers bring and the settings they recommend. | `hz system layer show` | `system_layer_show` | `GET /system/layer/show` | `GET /api/system/layer/show` |  | expert |

Health checks: reported by `hz system doctor`.

## `system.notify`

Pop up what deserves your attention: in the web app while it is open, else natively.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show the latest notices, whether each popped up and who showed it, and how they reach you. | `hz system notify list` | `system_notify_list` | — | `GET /api/system/notify/list` |  | expert |
| Mark notices as shown, so the daemon doesn't pop them up again. | `hz system notify ack` | `system_notify_ack` | — | `POST /api/system/notify/ack` | yes | expert |
| Record a test notice, report each way it can reach you, and exit 1 if nothing pops it up. | `hz system notify test` | — | — | `POST /api/system/notify/test` | yes | expert |
| Install the native notifier (Heinzel Notifier on macOS) and pop up a test notification now. | `hz system notify install` | — | — | — | yes | expert |
| Open the system's notification settings to allow hz's pop-ups (macOS: System Settings). | `hz system notify settings` | — | — | `POST /api/system/notify/settings` | yes | expert |

Settings: [`[system.notify]`](CONFIGURATION.md#systemnotify).

Health checks: reported by `hz system doctor`.

## `system.plugin`

Find, add, remove, enable, disable, list and scaffold plugins.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List installed plugins, enabled or not, with their source and the modules and adapters they bring. | `hz system plugin list` | `system_plugin_list` | `GET /system/plugin/list` | `GET /api/system/plugin/list` |  | expert |
| Show what a plugin would install, then add it with `--confirm`; adding it again replaces it. | `hz system plugin add` | `system_plugin_add` | — | `POST /api/system/plugin/add` | yes | expert |
| Add the plugin in a directory on this machine, to try a plugin you are writing. | `hz system plugin develop` | — | — | — | yes | expert |
| Remove a plugin. | `hz system plugin remove` | `system_plugin_remove` | — | `POST /api/system/plugin/remove` | yes | expert |
| Switch a plugin off without removing it, in `[hz] disabled_modules` of your user file. | `hz system plugin disable` | `system_plugin_disable` | — | `POST /api/system/plugin/disable` | yes | expert |
| Switch a disabled plugin on again. | `hz system plugin enable` | `system_plugin_enable` | — | `POST /api/system/plugin/enable` | yes | expert |
| List plugin changes made on this machine, oldest first: what was installed, from where, by whom. | `hz system plugin history` | `system_plugin_history` | — | `GET /api/system/plugin/history` |  | expert |
| Scaffold a plugin repository from the Heinzelmännchen template, asking for answers not given. | `hz system plugin new` | — | — | — | yes | expert |
| Find plugins to install: first-party ones, and community ones by querying the GitHub API. | `hz system plugin search` | `system_plugin_search` | `GET /system/plugin/search` | `GET /api/system/plugin/search` |  | expert |

Settings: [`[system.plugin]`](CONFIGURATION.md#systemplugin).

## `system.runner`

Run bounded agent sessions with Claude Code or a configured command.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Ask the bound agent runner, read-only and within a cost cap; this may cost money. | `hz system runner ask` | `system_runner_ask` | — | `POST /api/system/runner/ask` | yes | expert |

Settings: [`[system.runner]`](CONFIGURATION.md#systemrunner).

Health checks: reported by `hz system doctor`.

## `system.runner.chat`

Chat with the agent runner about where you are; it acts through hz once you confirm.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Open a chat session, optionally about a screen, a contract, a draft or a notification event. | `hz chat open` | — | — | `POST /api/system/runner/chat/open` | yes |  |
| Send a message in a chat session and wait for the answer; this may cost money. | `hz chat send` | — | — | `POST /api/system/runner/chat/send` | yes |  |
| Run an action the agent proposed, with exactly the arguments shown; only you can confirm. | `hz chat confirm` | — | — | `POST /api/system/runner/chat/confirm` | yes |  |
| Decline an action the agent proposed; it never runs. | `hz chat decline` | — | — | `POST /api/system/runner/chat/decline` | yes |  |
| Show a chat session: its context, messages, actions and costs. | `hz chat show` | — | — | `GET /api/system/runner/chat/show` |  | expert |
| List chat sessions, most recent first; they are kept for system.runner.chat.keep_days. | `hz chat list` | — | — | `GET /api/system/runner/chat/list` |  |  |
| Delete a chat session and everything it holds; returns the sessions left. | `hz chat remove` | — | — | `POST /api/system/runner/chat/remove` | yes | expert |

Settings: [`[system.runner.chat]`](CONFIGURATION.md#systemrunnerchat).

## `system.theme`

Pick how the web app looks: the built-in theme or one a plugin offers.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List the themes of the web app, with every colour, font and size, and which one is in effect. | `hz system theme list` | `system_theme_list` | `GET /system/theme/list` | `GET /api/system/theme/list` |  | expert |
| Show the signals themes follow now: the time of day, and the weather when a plugin publishes it. | `hz system theme signals` | `system_theme_signals` | `GET /system/theme/signals` | `GET /api/system/theme/signals` |  | expert |
| Use a theme in the web app: writes `[system.theme] name` to your configuration file. | `hz system theme use` | `system_theme_use` | `POST /system/theme/use` | `POST /api/system/theme/use` | yes | expert |

Settings: [`[system.theme]`](CONFIGURATION.md#systemtheme).

## `system.ui`

List what modules add to the web app: status items, header actions and Today widgets.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| List the status items modules add to the web app's status bar, with what each shows now. | `hz system ui status` | `system_ui_status` | — | `GET /api/system/ui/status` |  | expert |
| List the buttons modules add to the web app's header, with what each does and its badge. | `hz system ui actions` | `system_ui_actions` | — | `GET /api/system/ui/actions` |  | expert |
| List the widgets modules add to the web app's Today screen, with what each shows now. | `hz system ui widgets` | `system_ui_widgets` | — | `GET /api/system/ui/widgets` |  | expert |
| List every contribution to the web app, where it may show and whether it does; nothing runs. | `hz system ui list` | `system_ui_list` | — | `GET /api/system/ui/list` |  | expert |
| Show a contribution in a slot of the web app, such as the clock in the sidebar. | `hz system ui show` | `system_ui_show` | — | `POST /api/system/ui/show` | yes | expert |
| Hide a contribution from a slot of the web app, such as the clock from the footer. | `hz system ui hide` | `system_ui_hide` | — | `POST /api/system/ui/hide` | yes | expert |

Settings: [`[system.ui]`](CONFIGURATION.md#systemui).

Health checks: reported by `hz system doctor`.

## `team`

Your team's repository: create one (`hz team new`), join one by its URL (`hz team join`), add people to it.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Create a team repository from the template: you its first owner, its roster drafted for review. | `hz team new` | `team_new` | — | — | yes |  |
| Clone a team repository with your own access, find you in its roster, and make it your team. | `hz team join` | `team_join` | — | — | yes |  |
| Add someone to your team's roster, as one of its owners: a pull request the other owners see. | `hz team add` | `team_add` | — | — | yes |  |
| Show your team: its repository, checkout and members, and what is wrong with them. | `hz team show` | `team_show` | `GET /team/show` | `GET /api/team/show` |  |  |
| Make an existing checkout your team repository; replaces the deprecated `heinzel.team_repository`. | `hz team adopt` | `team_adopt` | — | — | yes | expert |

Settings: [`[team]`](CONFIGURATION.md#team).

Health checks: reported by `hz system doctor`.

## `zoom`

Read meetings from Zoom's meeting-summary emails: the meetings capability.

First-party plugin `heinzel-zoom`: `hz system plugin add zoom --confirm`.

| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |
| --- | --- | --- | --- | --- | --- | --- |
| Show this plugin's version, the Heinzelmännchen it runs on and where its meetings come from. | `hz zoom info` | `zoom_info` | `GET /zoom/info` | `GET /api/zoom/info` |  | expert |

Settings: [`[zoom]`](CONFIGURATION.md#zoom).

Health checks: reported by `hz system doctor`.
