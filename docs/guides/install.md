---
title: Install Heinzel
description: Install the standard Heinzel app, start the web workspace, and verify your first session.
---

# Your first Heinzelmännchen.

The standard app is the easiest start. It installs the core plus the default Clock, Resource, Tailscale and
Heinzelmännchen theme plugins from a versioned profile. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
and Python 3.12 or newer first. macOS and Linux are the documented command-line installation paths.

## Install the standard app

<!-- hz:install -->

The command is pinned to the newest complete standard release verified by this site's catalog refresh.
It checks the profile's artifact hashes and installs the default plugins together. Tailscale sharing remains opt-in;
installing its plugin does not make your workspace public.

```sh
hz --help
hz system doctor
hz ui
```

Open the local address printed by `hz ui`. Stop that session with **Ctrl-C**. Use the app's setup flow or run
`hz setup` to choose the services and AI access you want. A health check may report missing optional credentials;
configure the integrations you intend to use before relying on them.

[Continue to your first session →](first-steps.md)

## Building a plugin? Start with the SDK

```sh
uv tool install 'heinzel>=0.9.2,<0.10'
```

This installs the independently usable core without the standard plugin profile. You do not need a core checkout
to [scaffold, test or publish your own plugin](../PLUGIN_AUTHORING.md).

## Add a capability

```sh
hz system plugin search --text weather --json
hz system plugin add weather
hz system plugin add weather --confirm
```

The first `add` previews the installation. Review the source and compatibility before confirming it. Some optional
plugins require credentials or a compatible provider. The [catalog](../CATALOG.md) links to each owning source and
shows what its metadata declares; it does not certify all plugins against your configuration.

## If something is missing

| Symptom | Next step |
| --- | --- |
| `uv` is unavailable | Follow the official uv installation instructions linked above; open a new terminal afterward. |
| `hz` is unavailable after installation | Check `uv tool dir --bin` and put that directory on your shell's `PATH`. |
| A module reports unhealthy | Read `hz system doctor` and configure that integration with `hz setup`. |
| A private plugin cannot be found | Run `gh auth login` using an account allowed to read its repository. |
| You want exact command options | Use `hz --help` and the [CLI reference](../CLI_REFERENCE.md). |

For updates, removal and platform details, see the tested instructions in the
[repository README](../../README.md#install). The desktop lifecycle has
[explicit production gates](../DESKTOP_PRODUCTION_GATES.md); the local web command above is the starting path.
