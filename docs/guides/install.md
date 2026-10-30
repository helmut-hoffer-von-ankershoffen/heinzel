---
title: Install Heinzel
description: Install the standard Heinzel app, start the web workspace, and verify your first session.
---

# Your first Heinzelmännchen.

The standard app is the easiest start. It installs the core plus the default Clock, Resource, Tailscale and
Heinzelmännchen theme plugins from a versioned profile. Supported on **macOS and Linux**.
Windows may work, but is not supported. You do not need a separate Python installation.

## Install the standard app

If you already have [uv](https://docs.astral.sh/uv/getting-started/installation/), run:

<!-- hz:install -->

**Need uv installed for you?** Use the guided installer instead:

<!-- hz:install-script -->

It reuses uv when available, otherwise runs [Astral's official installer](https://docs.astral.sh/uv/getting-started/installation/).
It then runs the same app launcher. No administrator access is needed. You can
[inspect the script](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/install.sh) first. If you use wget:

```sh
wget -qO- https://helmut.hoffer-von-ankershoffen.me/heinzel/install.sh | sh
```

The first run selects the newest complete standard release automatically, verifies its artifact hashes,
installs and enables its default plugins, and opens the workspace. Later runs open the installed app.
Tailscale sharing remains opt-in;
installing its plugin does not make your workspace public.

Stop that session with **Ctrl-C**. Background operation is optional. Use the app's setup flow or run
`hz setup` to choose the services and AI access you want. `hz system doctor` may report missing optional credentials;
configure the integrations you intend to use before relying on them.

[Continue to your first session →](first-steps.md)

## Building a plugin? Start with the SDK

```sh
uv tool install heinzel
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
| `hz` is unavailable in another terminal | Run `uv tool update-shell`, then open a new terminal. |
| An older source installation cannot update | Stop the workspace, run `uv tool uninstall heinzel`, then use the standard installer above. Your configuration stays. |
| A module reports unhealthy | Read `hz system doctor` and configure that integration with `hz setup`. |
| A private plugin cannot be found | Run `gh auth login` using an account allowed to read its repository. |
| You want exact command options | Use `hz --help` and the [CLI reference](../CLI_REFERENCE.md). |

For updates, removal and platform details, see the tested instructions in the
[repository README](../../README.md#install). The desktop lifecycle has
[explicit production gates](../DESKTOP_PRODUCTION_GATES.md); the local web command above is the starting path.
