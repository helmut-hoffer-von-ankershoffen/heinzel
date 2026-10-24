# Modules

Generated from the code by `make docs`; CI fails when it is out of date. Every operation is the same in every head:
a `hz` command, an MCP tool (`hz system serve mcp`) and a REST endpoint (`hz system serve api`). Plugins add their own
modules; `hz system modules` lists what is installed.

| Module | What it does |
| --- | --- |
| [`atlassian`](#atlassian) | Read Jira issues and Confluence pages: the tracker and documents capabilities. |
| [`clock`](#clock) | What time it is for everyone on the team. |
| [`github`](#github) | Read pull requests and issues from GitHub: the code-host and tracker capabilities. |
| [`system`](#system) | Inspect, configure, diagnose and update Heinzelmännchen itself. |
| [`system.agent`](#systemagent) | Install the Heinzelmännchen skills for coding agents. |
| [`system.capability`](#systemcapability) | Show which adapters serve which capabilities, and who reads and writes what. |
| [`system.changelog`](#systemchangelog) | Release notes of the installed version. |
| [`system.config`](#systemconfig) | Show, get, set and initialize configuration. |
| [`system.credential`](#systemcredential) | Store and check the credentials modules need, in the OS keychain. |
| [`system.daemon`](#systemdaemon) | Run modules' jobs in the background, supervised by launchd or systemd. |
| [`system.doctor`](#systemdoctor) | Check that Heinzelmännchen and its modules are healthy. |
| [`system.plugin`](#systemplugin) | Find, add, remove, list and scaffold plugins. |
| [`system.runner`](#systemrunner) | Run bounded agent sessions with Claude Code or a configured command. |
| [`workflow`](#workflow) | Workflow contracts: Markdown files an agent drafts from, by hand or on your daemon's schedule (`hz workflow new` starts one). |

## `atlassian`

Read Jira issues and Confluence pages: the tracker and documents capabilities.

First-party plugin `heinzel-atlassian`: `hz system plugin add atlassian`.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| Show this plugin's version, the Heinzelmännchen it runs on and the Atlassian site and APIs it uses. | `hz atlassian info` | `atlassian_info` | `GET /atlassian/info` |  |

Settings: [`[atlassian]`](CONFIGURATION.md#atlassian).

Health checks: reported by `hz system doctor`.

## `clock`

What time it is for everyone on the team.

First-party plugin `heinzel-clock`: `hz system plugin add clock`.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| Show the time in each of the team's time zones. | `hz clock` | `clock` | `GET /clock` |  |

Settings: [`[clock]`](CONFIGURATION.md#clock).

Health checks: reported by `hz system doctor`.

Setup: `hz clock setup` (optional), also part of `hz setup`.

## `github`

Read pull requests and issues from GitHub: the code-host and tracker capabilities.

First-party plugin `heinzel-github`: `hz system plugin add github`.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| Show this plugin's version, the Heinzelmännchen it runs on and the GitHub API it uses. | `hz github info` | `github_info` | `GET /github/info` |  |

Settings: [`[github]`](CONFIGURATION.md#github).

Health checks: reported by `hz system doctor`.

## `system`

Inspect, configure, diagnose and update Heinzelmännchen itself.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| Show versions, interpreter, settings, modules and what modules report. | `hz system info` | `system_info` | `GET /system/info` |  |
| List every module and the lets it advertises. | `hz system modules` | `system_modules` | `GET /system/modules` |  |
| Ask where Heinzelmännchen was installed from for its latest release, and whether to update. | `hz system release` | `system_release` | `GET /system/release` |  |
| Update Heinzelmännchen, keeping installed plugins, and show the version now installed. | `hz system update` | `system_update` | `POST /system/update` | yes |

## `system.agent`

Install the Heinzelmännchen skills for coding agents.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| List the Agent Skills that ship with Heinzelmännchen. | `hz system agent skills` | `system_agent_skills` | `GET /system/agent/skills` |  |
| Copy Heinzelmännchen's skills into each configured agent skill directory. | `hz system agent install` | `system_agent_install` | `POST /system/agent/install` | yes |
| Remove Heinzelmännchen's skills from each configured agent skill directory. | `hz system agent uninstall` | `system_agent_uninstall` | `POST /system/agent/uninstall` | yes |

Settings: [`[system.agent]`](CONFIGURATION.md#systemagent).

## `system.capability`

Show which adapters serve which capabilities, and who reads and writes what.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| List capabilities, their bindings and adapters, and every requirement with its adapter and hosts. | `hz system capability list` | `system_capability_list` | `GET /system/capability/list` |  |

Health checks: reported by `hz system doctor`.

## `system.changelog`

Release notes of the installed version.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| Show the changelog of the installed version, as Markdown. | `hz system changelog` | `system_changelog` | `GET /system/changelog` |  |

## `system.config`

Show, get, set and initialize configuration.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| Show the configuration files Heinzelmännchen reads and every setting in effect. | `hz system config show` | `system_config_show` | `GET /system/config/show` |  |
| Write a configuration file with every setting commented out, ready to edit. | `hz system config init` | `system_config_init` | `POST /system/config/init` | yes |
| Show one setting in effect, with its environment variable. | `hz system config get` | `system_config_get` | `GET /system/config/get` |  |
| Change a setting in the user or project configuration file, after validating it. | `hz system config set` | `system_config_set` | `POST /system/config/set` | yes |
| Remove a setting from the user or project configuration file, so its default applies again. | `hz system config unset` | `system_config_unset` | `POST /system/config/unset` | yes |

## `system.credential`

Store and check the credentials modules need, in the OS keychain.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| List the credentials modules declare and whether each is stored, never their values. | `hz system credential list` | `system_credential_list` | `GET /system/credential/list` |  |
| Store a credential, read from standard input or a hidden prompt, never from the command line. | `hz system credential set` | — | — | yes |
| Remove a credential's value from the keychain. | `hz system credential remove` | — | — | yes |

Settings: [`[system.credential]`](CONFIGURATION.md#systemcredential).

Health checks: reported by `hz system doctor`.

## `system.daemon`

Run modules' jobs in the background, supervised by launchd or systemd.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| Install and load the launchd agent or systemd timer that ticks every few minutes. | `hz system daemon install` | — | — | yes |
| Unload and remove the daemon's unit; receipts and logs stay. | `hz system daemon uninstall` | — | — | yes |
| Show whether the daemon is installed, loaded and paused, its last tick and failing jobs. | `hz system daemon status` | `system_daemon_status` | `GET /system/daemon/status` |  |
| Show the latest lines of the daemon's log. | `hz system daemon logs` | `system_daemon_logs` | `GET /system/daemon/logs` |  |
| Run the jobs that are due and write a receipt for each; exits with 1 if any job failed. | `hz system daemon tick` | `system_daemon_tick` | — | yes |
| Pause the daemon: ticks run no job until resumed. | `hz system daemon pause` | `system_daemon_pause` | `POST /system/daemon/pause` | yes |
| Resume a paused daemon. | `hz system daemon resume` | `system_daemon_resume` | `POST /system/daemon/resume` | yes |
| List every job with its schedule, next slot and last run. | `hz system daemon jobs` | `system_daemon_jobs` | `GET /system/daemon/jobs` |  |

Settings: [`[system.daemon]`](CONFIGURATION.md#systemdaemon).

Health checks: reported by `hz system doctor`.

## `system.doctor`

Check that Heinzelmännchen and its modules are healthy.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| Run every module's health checks; exits with 1 if anything is down. | `hz system doctor` | `system_doctor` | `GET /system/doctor` |  |

Health checks: reported by `hz system doctor`.

## `system.plugin`

Find, add, remove, list and scaffold plugins.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| List installed plugins and the modules they bring. | `hz system plugin list` | `system_plugin_list` | `GET /system/plugin/list` |  |
| Add a plugin; adding one again replaces the installed version. | `hz system plugin add` | `system_plugin_add` | `POST /system/plugin/add` | yes |
| Remove a plugin. | `hz system plugin remove` | `system_plugin_remove` | `POST /system/plugin/remove` | yes |
| Scaffold a plugin repository from the Heinzelmännchen template, asking for answers not given. | `hz system plugin new` | — | — | yes |
| Find plugins to install: first-party ones, and community ones by querying the GitHub API. | `hz system plugin search` | `system_plugin_search` | `GET /system/plugin/search` |  |

Settings: [`[system.plugin]`](CONFIGURATION.md#systemplugin).

## `system.runner`

Run bounded agent sessions with Claude Code or a configured command.

Settings: [`[system.runner]`](CONFIGURATION.md#systemrunner).

Health checks: reported by `hz system doctor`.

## `workflow`

Workflow contracts: Markdown files an agent drafts from, by hand or on your daemon's schedule (`hz workflow new` starts one).

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| List the contracts in your and your team's workflows directories, with their schedule and problems. | `hz workflow list` | `workflow_list` | `GET /workflow/list` |  |
| Write a starter contract, valid as written and commented, into your workflows directory; creates it. | `hz workflow new` | `workflow_new` | — | yes |
| List the playbooks plugins contribute: asset types, template contracts and recommended settings. | `hz workflow playbooks` | `workflow_playbooks` | `GET /workflow/playbooks` |  |
| Find candidates for each playbook's asset types through the capabilities you have bound; reads only. | `hz workflow discover` | `workflow_discover` | — |  |
| Show a contract as the daemon runs it: schedule, requirements, adapters, readiness, next steps. | `hz workflow show` | `workflow_show` | `GET /workflow/show` |  |
| List the drafts runs saved locally, newest first, with their paths and records, or show the newest. | `hz workflow drafts` | `workflow_drafts` | — |  |
| Record how much of a draft you changed before sending it; `hz workflow show` reports it. | `hz workflow sent` | `workflow_sent` | — | yes |
| Check contracts and list every problem with file and line; exits with 1 on errors, so CI can gate. | `hz workflow check` | `workflow_check` | `GET /workflow/check` |  |
| Render a contract now: fetch its inputs, run the agent and save the draft; nothing is published. | `hz workflow run` | `workflow_run` | — | yes |

Settings: [`[workflow]`](CONFIGURATION.md#workflow).

Health checks: reported by `hz system doctor`.
