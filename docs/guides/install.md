---
title: Install Heinzelmännchen
description: Install the standard Heinzelmännchen app, start the web workspace, and verify your first session.
---

# Your first Heinzelmännchen.

The standard app installs the core and the default Clock, Resource, Tailscale and theme plugins. It runs on
**macOS and Linux**. Windows may work, but is not supported. You need no separate Python.

## Install the standard app

If you already have [uv](https://docs.astral.sh/uv/getting-started/installation/), run:

<!-- hz:install -->

`@latest` asks uv for the newest release on every start; a plain `uvx heinzel` keeps reusing whatever uv cached or
installed first, however old.

**Need uv installed for you?** Use the guided installer instead:

<!-- hz:install-script -->

It reuses uv, or runs [Astral's official installer](https://docs.astral.sh/uv/getting-started/installation/),
then starts the same launcher. No administrator access. You can
[inspect the script](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/install.sh) first. With wget:

```sh
wget -qO- https://heinzel.team/install.sh | sh
```

The first run picks the newest standard release, verifies its hashes, installs the default plugins and opens
the workspace. Later runs open the installed app. On Linux you need a systemd user manager in your session.
Tailscale sharing is opt-in; installing its plugin does not make your workspace public.

Stop with **Ctrl-C**. Background operation is optional. Run `hz setup` to choose services and AI access.
`hz system doctor` may report missing optional credentials; configure an integration before you rely on it.

[Continue to your first session →](first-steps.md)

## Stable by default, edge if you ask

Every command above, the app's update prompt and `hz system update` follow **stable**: releases that passed acceptance
on macOS and Linux. Nothing moves you off it by accident.

**Edge** is every green change, sooner and less tested. To choose it, or to go back:

```sh
hz system channel --to edge        # says what would change; changes nothing
hz system channel --to edge --yes  # chooses edge; the next hz system update installs it
hz system channel --to stable --yes
```

**Update channel** in Settings makes the same choice. It survives updates, and choosing a channel installs nothing
until you update. Going back to stable never downgrades: edge stays installed until stable is as new, and then the next
update moves you to stable. Your settings, plugins and data stay as they are.

## Building a plugin? Start with the SDK

```sh
uv tool install heinzel
```

This installs the core without the standard plugins. You need no core checkout to
[scaffold, test or publish a plugin](../PLUGIN_AUTHORING.md).

## Add a capability

```sh
hz system plugin search --text weather --json
hz system plugin add weather
hz system plugin add weather --confirm
```

The first `add` previews. Check the source and compatibility, then confirm. Some plugins need credentials. The
[catalog](../CATALOG.md) links each plugin's source and declared metadata; it does not certify a plugin for your setup.

## If something is missing

| Symptom | Next step |
| --- | --- |
| `uv` is unavailable | Use the guided installer above; it installs uv and continues immediately. |
| The Linux user manager is unavailable during installation | Open a user session with its systemd user manager, then retry. |
| `hz` is unavailable in another terminal | Run `uv tool update-shell`, then open a new terminal. |
| An older installation cannot update or start | Follow the [uninstall steps](../../README.md#install), then use the standard installer above. Your configuration stays. |
| A module reports unhealthy | Read `hz system doctor` and configure that integration with `hz setup`. |
| A private plugin cannot be found | Run `gh auth login` using an account allowed to read its repository. |
| You want exact command options | Use `hz --help` and the [CLI reference](../CLI_REFERENCE.md). |

For updates, removal and platform details, see the tested instructions in the
[repository README](../../README.md#install). The desktop lifecycle has
[explicit production gates](../DESKTOP_PRODUCTION_GATES.md); the local web command above is the starting path.
