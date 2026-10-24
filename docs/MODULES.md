# Modules

Generated from the code by `make docs`; CI fails when it is out of date. Every operation is the same in every head:
a `hz` command, an MCP tool (`hz system serve mcp`) and a REST endpoint (`hz system serve api`). Plugins add their own
modules; `hz system modules` lists what is installed.

| Module | What it does |
| --- | --- |
| [`clock`](#clock) | What time it is for everyone on the team. |
| [`system`](#system) | Inspect, configure, diagnose and update Heinzelmännchen itself. |
| [`system.agent`](#systemagent) | Install the Heinzelmännchen skills for coding agents. |
| [`system.changelog`](#systemchangelog) | Release notes of the installed version. |
| [`system.config`](#systemconfig) | Show, get, set and initialize configuration. |
| [`system.credential`](#systemcredential) | Store and check the credentials modules need, in the OS keychain. |
| [`system.doctor`](#systemdoctor) | Check that Heinzelmännchen and its modules are healthy. |
| [`system.plugin`](#systemplugin) | Find, add, remove, list and scaffold plugins. |

## `clock`

What time it is for everyone on the team.

First-party plugin `heinzel-clock`: `hz system plugin add clock`.

| Operation | CLI | MCP tool | REST | Changes state |
| --- | --- | --- | --- | --- |
| Show the time in each of the team's time zones. | `hz clock` | `clock` | `GET /clock` |  |

Settings: [`[clock]`](CONFIGURATION.md#clock).

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
| Remove a credential's value from the keychain. | `hz system credential remove` | `system_credential_remove` | `POST /system/credential/remove` | yes |

Settings: [`[system.credential]`](CONFIGURATION.md#systemcredential).

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
