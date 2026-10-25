---
name: scaffold-plugin
description: Create a new Heinzelmännchen plugin repository from the copier template, or update an existing plugin to the latest template. Use when asked to create, scaffold or bootstrap a Heinzelmännchen plugin, or to bring a plugin up to date with Heinzelmännchen's standards.
---

# Scaffold a plugin

## New plugin

1. `hz system plugin new ../heinzel-<name>` (or `uvx copier copy gh:helmut-hoffer-von-ankershoffen/heinzel ../heinzel-<name>`)
   and answer the questions: plugin name, description, author, GitHub owner.
2. `cd ../heinzel-<name> && git init -b main && make setup && make check`.
3. Implement modules in `src/heinzel_<name>/` (skills `add-module`, `add-operation` work there too).
4. Try it with Heinzelmännchen: `hz system plugin develop .` then `hz <name> --help`.
5. Create the GitHub repository, push, and cut `v0.1.0` with the Release workflow.

## Update an existing plugin

`make update-template` in the plugin repository (clean working tree), resolve conflicts, `make check`, commit as
`chore(template): update to heinzel vX.Y.Z`.

Template changes are made in Heinzelmännchen's `template/` directory and checked by `make template-test`.
