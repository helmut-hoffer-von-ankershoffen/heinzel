---
title: Install Heinzel
description: Install the standard Heinzel app, start the web workspace, and verify your first session.
---

# Install Heinzel.

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

First interactive setup asks for missing name, team name and required profile details through normal configuration.
Existing values are preserved. Setup is complete only when system health is green.

The interactive launcher offers the scheduled daemon, web server after login and native notifier separately;
each prompt defaults to **Yes**. Decline any choice with **No**. All selected installation runs in the verified
persistent standard app. Noninteractive, help and machine-readable calls make no automatic service choices.
Unsupported native notification platforms or desktops are reported; OS permission and visible delivery remain
separate checks.

If macOS has notifications turned off for Heinzel Notifier, open **System Settings → Notifications → Heinzel
Notifier** and enable **Allow notifications**. Run `hz system notify install` to send a test notification, then
run `uvx heinzel@latest` again to finish setup.

`hz ui` displays help. Use `hz ui serve` for foreground serving, stopped with **Ctrl-C**, or `hz ui open` to open
the installed service and otherwise serve in the foreground. `hz ui install` is available without a preview flag;
`hz ui status`, `restart` and `uninstall` manage its owned service. Run `hz setup` to configure AI access.
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

## Tools the app uses

Git and the GitHub CLI (`gh`) are part of the standard app: they clone team repositories and plugin sources.
On macOS the installer shows what is missing and installs it with [Homebrew](https://brew.sh); it does not install
Homebrew itself: without it, the installer links to brew.sh and the app still installs.

On Linux it prints the steps for your distribution and installs nothing. Add `--no-tools` to `hz-install` to only check. After you install
a tool yourself, `hz system prerequisite check` tells you when everything is usable. Installing `gh` never signs you in:
run `gh auth login` yourself.

Features bring their own tools only when you choose them: the Claude Code or Codex command line for the agent runner
you select, for example. `hz setup` shows what a step needs, asks before installing it on macOS, and leaves the
program in place if you later remove a plugin. Each plugin's page lists its prerequisites.

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

### Notification history needs recovery

If `hz system doctor` reports **notification store: down**, hz cannot safely read the durable record of notices
already queued. Some integrity failures block notification writes to avoid repeating a notice whose result is uncertain.
Reinstalling the native notifier does not repair this history. Doctor leaves the existing state unchanged.

For a read-only, bounded check, doctor verifies at most 4 MiB of notification state and rejects linked, nonregular,
public or inaccessible store paths. Larger histories report verification unavailable; this does not prove corruption
and does not impose a new limit on durable writes. Preserve the full history for review rather than trimming keys.

Preserve the existing notification store and its `durable-notices` marker. Recovery requires a coherent, trusted
backup of the same profile: the notification state must include both its notices and complete durable replay-key set,
with the corresponding marker. A legacy list of notices alone is insufficient; display history is bounded and cannot
reconstruct missing replay keys. Do not delete the marker, reset the store or convert a legacy list to an empty-key
record to silence the warning.

Before any restoration, stop writers using that profile through their supported controls and ensure they use the
current hz version. Have the backup and restoration procedure reviewed before changing the preserved state, then run
`hz system doctor` again before resuming notification delivery. If no coherent backup is available, keep the store
unchanged and ask for recovery help.


On first interactive setup, provide your real name, team and required profile details. Existing values are kept.
Choose **Set up AI later** to use Heinzel without an AI account; this saves an explicit unconfigured choice for uses
that run only when asked. Existing provider selections are kept.

Asking or sending a chat still requires configured
access, and an active scheduled Heinzel still reports missing capabilities. Setup finishes only after actual system
health is up. A bare noninteractive first use with missing required profile details returns an incomplete result
without prompting or opening the app.

When upgrading an existing installation, an old explicit foreground vector such as `hz ui --port 0 --no-browser` forwards to
the same serving implementation with a deprecation hint. New scripts and applications use `hz ui serve`; bare
`hz ui` remains group help and never starts a listener.

## Uninstall offline

For a verified standard installation, run `hz system uninstall`.
It needs no network or running web UI. Your logged-in user service manager must be available: it stops only the installation's verified owned web, daemon and calendar-reminder jobs across all recorded profiles, then removes its uv tool
and command scripts. Your data, configuration, agent skills, notifier and private recovery history stay.

Core installs an exact pinned uv dependency. Removal authenticates its native bytes and stages a private copy before
stopping any service; it never executes uv from PATH. A missing artifact, changed SDK, foreign service or
unsupported installation refuses before unsafe removal. Unmarked SDK and desktop runtime installations use their
own local installer. No purge or network bootstrap runs as part of this command.

If removal is interrupted, follow the concrete offline recovery command printed by the failure. Its private script
uses the already installed external Python and retained verified remover, so it remains usable after the command's
SDK is gone. Keep its journal and integration files until recovery reports completion.


Older owned web launchers remain inspectable after an upgrade. Run `hz ui status` to find the current port, then use
`hz ui install --port 8738` with that value in place of 8738, or restart the ready owned service, to update its launcher
before loading current code. An interrupted update prints the exact install or uninstall command to retry; retain its
journal and recovery fences until that
operation completes. Offline uninstall retains compatible fences for older shells through verified SDK removal and
integration cleanup, and its copied recovery remains usable after the SDK is gone.
