# Modules

Generated from the code by `make docs`; CI fails when it is out of date. Every operation is the same in every head:
a `hz` command, an MCP tool (`hz system serve mcp`), a REST endpoint (`hz system serve api`) and an endpoint of the web
app's API (`hz ui`, under `/api`, behind its session token). Plugins add their own modules; `hz system modules` lists
what is installed.

| Module | What it does |
| --- | --- |
| [`atlassian`](#atlassian) | Read Jira issues and Confluence pages through acli: the tracker and documents capabilities. |
| [`clock`](#clock) | What time it is for everyone on the team. |
| [`github`](#github) | Read pull requests and issues from GitHub: the code-host and tracker capabilities. |
| [`google`](#google) | Read Google Docs, Sheets, Drive, Calendar and Gmail: the documents, calendar and mail capabilities. |
| [`org`](#org) | Your profile, and the team, org and company you work with. |
| [`system`](#system) | Inspect, configure, diagnose and update Heinzelmännchen itself. |
| [`system.agent`](#systemagent) | Install the Heinzelmännchen skills for coding agents. |
| [`system.capability`](#systemcapability) | Show which adapters serve which capabilities, and who reads and writes what. |
| [`system.changelog`](#systemchangelog) | Release notes of the installed version and of newer releases. |
| [`system.config`](#systemconfig) | Show, get, set and initialize configuration. |
| [`system.credential`](#systemcredential) | Store and check the credentials modules need, in the OS keychain. |
| [`system.daemon`](#systemdaemon) | Run modules' jobs in the background, supervised by launchd or systemd. |
| [`system.directory`](#systemdirectory) | Guess reporting lines from recurring 1:1s in your calendar, for setup to confirm. |
| [`system.doctor`](#systemdoctor) | Check that Heinzelmännchen and its modules are healthy. |
| [`system.layer`](#systemlayer) | Show the layer plugins installed and the stack you are in, general to specific. |
| [`system.plugin`](#systemplugin) | Find, add, remove, list and scaffold plugins. |
| [`system.runner`](#systemrunner) | Run bounded agent sessions with Claude Code or a configured command. |
| [`team`](#team) | Your team's repository: create one (`hz team new`), join one by its URL (`hz team join`), add people to it. |
| [`workflow`](#workflow) | Workflow contracts: Markdown files an agent drafts from, by hand or on your daemon's schedule (`hz workflow new` starts one). |
| [`zoom`](#zoom) | Read meetings from Zoom's meeting-summary emails: the meetings capability. |

## `atlassian`

Read Jira issues and Confluence pages through acli: the tracker and documents capabilities.

First-party plugin `heinzel-atlassian`: `hz system plugin add atlassian`.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Show this plugin's version, the Atlassian site and the acli commands it runs. | `hz atlassian info` | `atlassian_info` | `GET /atlassian/info` | `GET /api/atlassian/info` |  |

Settings: [`[atlassian]`](CONFIGURATION.md#atlassian).

Health checks: reported by `hz system doctor`.

## `clock`

What time it is for everyone on the team.

First-party plugin `heinzel-clock`: `hz system plugin add clock`.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Show the time in each of the team's time zones. | `hz clock` | `clock` | `GET /clock` | `GET /api/clock` |  |

Settings: [`[clock]`](CONFIGURATION.md#clock).

Health checks: reported by `hz system doctor`.

Setup: `hz setup clock` (optional), also part of `hz setup`.

## `github`

Read pull requests and issues from GitHub: the code-host and tracker capabilities.

First-party plugin `heinzel-github`: `hz system plugin add github`.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Show this plugin's version, the Heinzelmännchen it runs on and the GitHub host it reads. | `hz github info` | `github_info` | `GET /github/info` | `GET /api/github/info` |  |

Settings: [`[github]`](CONFIGURATION.md#github).

Health checks: reported by `hz system doctor`.

## `google`

Read Google Docs, Sheets, Drive, Calendar and Gmail: the documents, calendar and mail capabilities.

First-party plugin `heinzel-google`: `hz system plugin add google`.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Show this plugin's version, the Heinzelmännchen it runs on and the gws, scopes and APIs it uses. | `hz google info` | `google_info` | `GET /google/info` | `GET /api/google/info` |  |
| List the Google Docs and Sheets you can open that changed recently, newest first. | `hz google files` | `google_files` | `GET /google/files` | `GET /api/google/files` |  |

Settings: [`[google]`](CONFIGURATION.md#google).

Health checks: reported by `hz system doctor`.

Setup: `hz setup google` (optional), also part of `hz setup`.

## `org`

Your profile, and the team, org and company you work with.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Show your profile: who you are, your role, time zone and working hours, and what is still missing. | `hz org show` | `org_show` | — | `GET /api/org/show` |  |
| Show what your team recommends beside what you have; apply only what you accept, to your user file. | `hz org sync` | `org_sync` | — | — | yes |

Settings: [`[org]`](CONFIGURATION.md#org).

Health checks: reported by `hz system doctor`.

Setup: `hz setup org` (required), also part of `hz setup`.

## `system`

Inspect, configure, diagnose and update Heinzelmännchen itself.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Show versions, interpreter, settings, modules and what modules report. | `hz system info` | `system_info` | `GET /system/info` | `GET /api/system/info` |  |
| List every module and the lets it advertises. | `hz system modules` | `system_modules` | `GET /system/modules` | `GET /api/system/modules` |  |
| Ask where Heinzelmännchen was installed from for its latest release, and whether to update. | `hz system release` | `system_release` | `GET /system/release` | `GET /api/system/release` |  |
| Update Heinzelmännchen, keeping plugins; see what it brings with `hz system changelog --newer`. | `hz system update` | `system_update` | `POST /system/update` | `POST /api/system/update` | yes |

## `system.agent`

Install the Heinzelmännchen skills for coding agents.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| List the Agent Skills that ship with Heinzelmännchen. | `hz system agent skills` | `system_agent_skills` | `GET /system/agent/skills` | `GET /api/system/agent/skills` |  |
| Copy Heinzelmännchen's skills into each configured agent skill directory. | `hz system agent install` | `system_agent_install` | `POST /system/agent/install` | `POST /api/system/agent/install` | yes |
| Remove Heinzelmännchen's skills from each configured agent skill directory. | `hz system agent uninstall` | `system_agent_uninstall` | `POST /system/agent/uninstall` | `POST /api/system/agent/uninstall` | yes |

Settings: [`[system.agent]`](CONFIGURATION.md#systemagent).

## `system.capability`

Show which adapters serve which capabilities, and who reads and writes what.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| List capabilities, their bindings and adapters, and every requirement with its adapter and hosts. | `hz system capability list` | `system_capability_list` | `GET /system/capability/list` | `GET /api/system/capability/list` |  |

Health checks: reported by `hz system doctor`.

## `system.changelog`

Release notes of the installed version and of newer releases.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Show the installed version's changelog as Markdown, or with `--newer` what newer releases bring. | `hz system changelog` | `system_changelog` | `GET /system/changelog` | `GET /api/system/changelog` |  |

Settings: [`[system.changelog]`](CONFIGURATION.md#systemchangelog).

## `system.config`

Show, get, set and initialize configuration.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Show the configuration files Heinzelmännchen reads and every setting in effect. | `hz system config show` | `system_config_show` | `GET /system/config/show` | `GET /api/system/config/show` |  |
| Write a configuration file with every setting commented out, ready to edit. | `hz system config init` | `system_config_init` | `POST /system/config/init` | `POST /api/system/config/init` | yes |
| Show one setting in effect, with its environment variable. | `hz system config get` | `system_config_get` | `GET /system/config/get` | `GET /api/system/config/get` |  |
| Change a setting in the user or project configuration file, after validating it. | `hz system config set` | `system_config_set` | `POST /system/config/set` | `POST /api/system/config/set` | yes |
| Remove a setting from the user or project configuration file, so its default applies again. | `hz system config unset` | `system_config_unset` | `POST /system/config/unset` | `POST /api/system/config/unset` | yes |

## `system.credential`

Store and check the credentials modules need, in the OS keychain.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| List the credentials modules declare and whether each is stored, never their values. | `hz system credential list` | `system_credential_list` | `GET /system/credential/list` | `GET /api/system/credential/list` |  |
| Store a credential, read from standard input or a hidden prompt, never from the command line. | `hz system credential set` | — | — | — | yes |
| Remove a credential's value from the keychain. | `hz system credential remove` | — | — | — | yes |

Settings: [`[system.credential]`](CONFIGURATION.md#systemcredential).

Health checks: reported by `hz system doctor`.

## `system.daemon`

Run modules' jobs in the background, supervised by launchd or systemd.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Install and load the launchd agent or systemd timer that ticks every few minutes. | `hz system daemon install` | — | — | — | yes |
| Unload and remove the daemon's unit; receipts and logs stay. | `hz system daemon uninstall` | — | — | — | yes |
| Show whether the daemon is installed, loaded and paused, its last tick and failing jobs. | `hz system daemon status` | `system_daemon_status` | `GET /system/daemon/status` | `GET /api/system/daemon/status` |  |
| Show the latest lines of the daemon's log. | `hz system daemon logs` | `system_daemon_logs` | `GET /system/daemon/logs` | `GET /api/system/daemon/logs` |  |
| Run the jobs that are due and write a receipt for each; exits with 1 if any job failed. | `hz system daemon tick` | `system_daemon_tick` | — | — | yes |
| Pause the daemon: ticks run no job until resumed. | `hz system daemon pause` | `system_daemon_pause` | `POST /system/daemon/pause` | `POST /api/system/daemon/pause` | yes |
| Resume a paused daemon. | `hz system daemon resume` | `system_daemon_resume` | `POST /system/daemon/resume` | `POST /api/system/daemon/resume` | yes |
| List every job with its schedule, next slot and last run. | `hz system daemon jobs` | `system_daemon_jobs` | `GET /system/daemon/jobs` | `GET /api/system/daemon/jobs` |  |

Settings: [`[system.daemon]`](CONFIGURATION.md#systemdaemon).

Health checks: reported by `hz system doctor`.

## `system.directory`

Guess reporting lines from recurring 1:1s in your calendar, for setup to confirm.

Settings: [`[system.directory]`](CONFIGURATION.md#systemdirectory).

## `system.doctor`

Check that Heinzelmännchen and its modules are healthy.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Run every module's health checks; exits with 1 if anything is down. | `hz system doctor` | `system_doctor` | `GET /system/doctor` | `GET /api/system/doctor` |  |

Health checks: reported by `hz system doctor`.

## `system.layer`

Show the layer plugins installed and the stack you are in, general to specific.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| List the installed layer plugins: where each sits, what it brings and what is wrong with the tree. | `hz system layer list` | `system_layer_list` | `GET /system/layer/list` | `GET /api/system/layer/list` |  |
| Show your stack: company, org and site, team; what the layers bring and the settings they recommend. | `hz system layer show` | `system_layer_show` | `GET /system/layer/show` | `GET /api/system/layer/show` |  |

Health checks: reported by `hz system doctor`.

## `system.plugin`

Find, add, remove, list and scaffold plugins.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| List installed plugins and the modules they bring. | `hz system plugin list` | `system_plugin_list` | `GET /system/plugin/list` | `GET /api/system/plugin/list` |  |
| Add a plugin; adding one again replaces the installed version. | `hz system plugin add` | `system_plugin_add` | `POST /system/plugin/add` | `POST /api/system/plugin/add` | yes |
| Remove a plugin. | `hz system plugin remove` | `system_plugin_remove` | `POST /system/plugin/remove` | `POST /api/system/plugin/remove` | yes |
| Scaffold a plugin repository from the Heinzelmännchen template, asking for answers not given. | `hz system plugin new` | — | — | — | yes |
| Find plugins to install: first-party ones, and community ones by querying the GitHub API. | `hz system plugin search` | `system_plugin_search` | `GET /system/plugin/search` | `GET /api/system/plugin/search` |  |

Settings: [`[system.plugin]`](CONFIGURATION.md#systemplugin).

## `system.runner`

Run bounded agent sessions with Claude Code or a configured command.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Ask the bound agent runner, read-only and within a cost cap; this may cost money. | `hz system runner ask` | `system_runner_ask` | — | `POST /api/system/runner/ask` | yes |

Settings: [`[system.runner]`](CONFIGURATION.md#systemrunner).

Health checks: reported by `hz system doctor`.

## `team`

Your team's repository: create one (`hz team new`), join one by its URL (`hz team join`), add people to it.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Create a team repository from the template: you its first owner, its roster drafted for review. | `hz team new` | `team_new` | — | — | yes |
| Clone a team repository with your own access, find you in its roster, and make it your team. | `hz team join` | `team_join` | — | — | yes |
| Add someone to your team's roster, as one of its owners: a pull request the other owners see. | `hz team add` | `team_add` | — | — | yes |
| Show your team: its repository, checkout and members, and what is wrong with them. | `hz team show` | `team_show` | `GET /team/show` | `GET /api/team/show` |  |
| Make an existing checkout your team repository; replaces the deprecated `workflow.team_repository`. | `hz team adopt` | `team_adopt` | — | — | yes |

Settings: [`[team]`](CONFIGURATION.md#team).

Health checks: reported by `hz system doctor`.

## `workflow`

Workflow contracts: Markdown files an agent drafts from, by hand or on your daemon's schedule (`hz workflow new` starts one).

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| List the contracts in your and your team's workflows directories, with their schedule and problems. | `hz workflow list` | `workflow_list` | `GET /workflow/list` | `GET /api/workflow/list` |  |
| Write a starter contract, or a playbook's template (--template), into your workflows directory. | `hz workflow new` | `workflow_new` | — | — | yes |
| List the playbooks plugins contribute: asset types, template contracts and recommended settings. | `hz workflow playbooks` | `workflow_playbooks` | `GET /workflow/playbooks` | `GET /api/workflow/playbooks` |  |
| Find candidates for each playbook's asset types through the capabilities you have bound; reads only. | `hz workflow discover` | `workflow_discover` | — | `GET /api/workflow/discover` |  |
| Show a contract as the daemon runs it: schedule, requirements, adapters, readiness, next steps. | `hz workflow show` | `workflow_show` | `GET /workflow/show` | `GET /api/workflow/show` |  |
| List the drafts runs saved locally, newest first, with their paths and records, or show the newest. | `hz workflow drafts` | `workflow_drafts` | — | — |  |
| Record how much of a draft you changed before sending it; `hz workflow show` reports it. | `hz workflow sent` | `workflow_sent` | — | — | yes |
| Show the workflow your contracts make: what each maintains and reads, cycles, unmaintained inputs. | `hz workflow graph` | `workflow_graph` | `GET /workflow/graph` | `GET /api/workflow/graph` |  |
| Check contracts and list every problem with file and line; exits with 1 on errors, so CI can gate. | `hz workflow check` | `workflow_check` | `GET /workflow/check` | `GET /api/workflow/check` |  |
| Render a contract now: fetch its inputs, run the agent and save the draft; nothing is published. | `hz workflow run` | `workflow_run` | — | — | yes |
| Delete drafts and records older than `[workflow] retention_days` (default 90); receipts are kept. | `hz workflow prune` | `workflow_prune` | — | — | yes |

Settings: [`[workflow]`](CONFIGURATION.md#workflow).

Health checks: reported by `hz system doctor`.

## `zoom`

Read meetings from Zoom's meeting-summary emails: the meetings capability.

First-party plugin `heinzel-zoom`: `hz system plugin add zoom`.

| Operation | CLI | MCP tool | REST | Web | Changes state |
| --- | --- | --- | --- | --- | --- |
| Show this plugin's version, the Heinzelmännchen it runs on and where its meetings come from. | `hz zoom info` | `zoom_info` | `GET /zoom/info` | `GET /api/zoom/info` |  |

Settings: [`[zoom]`](CONFIGURATION.md#zoom).

Health checks: reported by `hz system doctor`.
