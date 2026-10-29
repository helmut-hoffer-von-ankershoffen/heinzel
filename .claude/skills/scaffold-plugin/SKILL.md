---
name: scaffold-plugin
description: Create a new Heinzelmännchen plugin repository from the copier template, or update an existing plugin to the latest template. Use when asked to create, scaffold or bootstrap a Heinzelmännchen plugin, or to bring a plugin up to date with Heinzelmännchen's standards.
---

# Scaffold a plugin

## New plugin

Read [the author guide](../../../docs/PLUGIN_AUTHORING.md), including the separate public/private repository paths.

1. Outside an existing Git repository, run
   `hz system plugin new heinzel-<name> --defaults --data plugin_name=<name>`. The minimal preset is one operation
   and behavior test; use `--preset contributions` only when settings, views, Today or notifications are useful.
2. `cd heinzel-<name> && make setup && make check-fast`. Setup initializes Git and creates the owning lock and
   environment. Follow generated AGENTS.md; CLAUDE.md uses the same instructions.
3. Edit the owning operation and behavior test. Use public `heinzel` / `heinzel.kernel` contracts. A model or service
   is optional. Add ordinary dependencies to the owning pyproject, then `make lock`.
4. `make fix`, `make check-fast`, `make check`, `make build`, `make acceptance`; `make preview` uses isolated state.
   These commands use installed artifacts and never run the core suite. `CORE=<wheel>` selects a candidate SDK.
5. Host in the requested public or private repository, and add topic `heinzel-plugin` for catalog discovery.
   Private installation uses the installing person's `gh` login. `make release` previews; after review and merge,
   `make publish` selects private GitHub or public PyPI publication. Public PyPI needs the owning trusted publisher;
   private sources must stay off PyPI. Independent tags are `<distribution>/v<version>`.

Inside an existing repository, use `hz system plugin new plugins/heinzel-<name> --defaults --layout collection
--data plugin_name=<name>`, or the host's optional `new` shortcut with `NAME=<name>`. Keep all feature files in the new
directory and run the same owning Make commands from there. Root `NAME=<owner>` shortcuts are optional. Never
create an embedded repository or copy parent workflows.

## Update an existing plugin

From a clean owning repository, run `uvx copier update --defaults --vcs-ref vX.Y.Z`, resolve conflicts, refresh its
lock and run owning checks/build/acceptance. Commit as `chore(template): update to heinzel vX.Y.Z`. Retained governed
repositories also have the `make update-template` wrapper. Do not replace owning source or tests with another plugin.

New templates live in `starter/`; `template/` retains the governed layout. `make template-test` checks both against
installed core/toolkit artifacts, including a new authored operation in each small preset and layout.
