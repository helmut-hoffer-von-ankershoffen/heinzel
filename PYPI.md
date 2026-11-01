# Heinzelmännchen

**Less busywork. More good work.** Heinzelmännchen is an open-source crew of AI helpers that drafts your team's busywork and asks before anything leaves your machine.
Connect your tools, put repeatable work into workflows, and keep people in control.

![Heinzelmännchen helpers](https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/main/docs/assets/pypi-hero.png)

[Install & explore](https://heinzel.team/guides/install/) ·
[Plugin catalog](https://heinzel.team/CATALOG/) ·
[Build a plugin](https://heinzel.team/PLUGIN_AUTHORING/)

## Install

Supported on **macOS and Linux**. Windows may work, but is not supported.
If you already have [uv](https://docs.astral.sh/uv/getting-started/installation/), run:

<!-- install:start -->
<!-- docs-test -->
```sh
uvx heinzel@latest
```
<!-- install:end -->

**Need uv installed for you?** Use the guided installer instead:

<!-- install-script:start -->
```sh
curl -LsSf https://heinzel.team/install.sh | sh
```
<!-- install-script:end -->

It reuses uv when available, otherwise runs [Astral's official installer](https://docs.astral.sh/uv/getting-started/installation/).
It then runs the same app launcher. No administrator access or separate Python installation is needed.
You can [inspect the script](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/install.sh) first.

One command selects the newest published standard app, verifies its artifacts, installs its default plugins and opens the workspace.
You do not need to choose versions, clone a repository or configure a build environment.
Run the same command later to open the installed app. **Ctrl-C** stops the foreground session;
background operation remains optional. Use the setup flow to configure the services you want.
Optional integrations may need credentials. Installing the Tailscale plugin does not enable sharing.
On Linux, standard installation needs an accessible systemd user manager in your user session.
In a headless session without one, open a user session with its service manager and retry.

## Work where you work

- **One operation, several ways in.** Typed Python operations can become CLI commands, MCP tools and REST endpoints.
- **A web workspace.** Today, contracts and team context bring the work into view.
- **Plugins that own their functionality.** Add operations, views, widgets, notifications and integrations.
- **Themes with personality.** Palettes, artwork and soundscapes belong to their themes.
- **Independent development.** Build, test and release plugins against an installed SDK.

![Team graph in the Heinzelmännchen workspace with fictional data](https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/main/docs/assets/website/product-workspace.jpg)

This is the foundation: kernel, heads, workflows and extension points. The broader vision is accountable,
composable helpers that draft a product team's recurring work for a person's approval. [See the vision and architecture](https://heinzel.team/VISION/).

## Extend it

Plugins can live in their own public or private repositories, carry their own dependencies and release on their
own schedule. [The authoring guide](https://heinzel.team/PLUGIN_AUTHORING/) covers
scaffolding, local preview, tests, builds, publication and catalog discovery.

For a minimal SDK without the standard plugins:

```sh
uv tool install heinzel
```

[Contribute](https://heinzel.team/guides/contribute/) ·
[Source](https://github.com/helmut-hoffer-von-ankershoffen/heinzel) ·
[Issues](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/issues) ·
[License](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/LICENSE)
