---
name: hz-init
description: Install or update Heinzelmännchen, the open-source AI-driven operating system for product development teams, on this machine - the hz CLI via uv, chosen plugins, a starter configuration and the Heinzelmännchen skills for coding agents. Use when the user asks to install, set up, initialize, update or repair Heinzelmännchen, or types /hz-init.
---

# hz-init

Set up Heinzelmännchen end to end and verify it. Ask before anything that changes the machine beyond these steps.

## 1. Prerequisites

- `uv --version`. Missing → ask the user to install it (`brew install uv` on macOS, or
  `curl -LsSf https://astral.sh/uv/install.sh | sh`); don't pipe to a shell without their go-ahead.
- `git --version`.

## 2. Install

Ask which plugins the user wants (their team usually documents the specs) and whether to pin a Heinzelmännchen release. Then run
the script that ships with this skill; it installs or updates the CLI with uv, adds the plugins, writes a starter
user configuration if there is none and installs Heinzelmännchen's agent skills:

```bash
bash "<this skill's directory>/scripts/install.sh" [--version vX.Y.Z] [--plugin <name> | --plugin git+https://github.com/<owner>/heinzel-<name>@<version>]...
```

Plugins survive `uv tool upgrade heinzel`. If the script reports that uv's tool directory is not on `PATH`, run
`uv tool update-shell` with the user's go-ahead.

## 3. Verify and report

```bash
hz --version
hz system info
hz system modules
```

Report the version, the plugins, the configuration files (`hz system config show`), and anything that failed with
its fix. To remove Heinzelmännchen later, use the `hz-uninstall` skill.
