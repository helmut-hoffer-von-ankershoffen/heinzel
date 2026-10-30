# Heinzelmännchen

**Less busywork. More good work.** Open-source, local-first helpers for product teams.
Connect your tools, put repeatable work into workflows, and keep people in control.

![Heinzelmännchen helpers](https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/main/docs/assets/pypi-hero.png)

[Install & explore](https://helmut.hoffer-von-ankershoffen.me/heinzel/guides/install/) ·
[Plugin catalog](https://helmut.hoffer-von-ankershoffen.me/heinzel/CATALOG/) ·
[Build a plugin](https://helmut.hoffer-von-ankershoffen.me/heinzel/PLUGIN_AUTHORING/)

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
curl -LsSf https://helmut.hoffer-von-ankershoffen.me/heinzel/install.sh | sh
```
<!-- install-script:end -->

It reuses uv when available, otherwise runs [Astral's official installer](https://docs.astral.sh/uv/getting-started/installation/).
It then runs the same app launcher. No administrator access or separate Python installation is needed.
You can [inspect the script](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/install.sh) first.

One command installs the newest complete, verified standard app and its default plugins, then opens the workspace.
You do not need to choose versions, clone a repository or configure a build environment.
Run the same command later to open the installed app. **Ctrl-C** stops the foreground session;
background operation remains optional. Use the setup flow to configure the services you want.
Optional integrations may need credentials. Installing the Tailscale plugin does not enable sharing.

## Work where you work

- **One operation, several ways in.** Typed Python operations can become CLI commands, MCP tools and REST endpoints.
- **A web workspace.** Today, contracts and team context bring the work into view.
- **Plugins that own their functionality.** Add operations, views, widgets, notifications and integrations.
- **Themes with personality.** Palettes, artwork and soundscapes belong to their themes.
- **Independent development.** Build, test and release plugins against an installed SDK.

![Team graph in the Heinzel workspace with fictional data](https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/main/docs/assets/website/product-workspace.jpg)

This is the foundation: kernel, heads, workflows and extension points. The broader vision is an accountable,
composable AI operating system for product teams. [See the vision and architecture](https://helmut.hoffer-von-ankershoffen.me/heinzel/VISION/).

## Extend it

Plugins can live in their own public or private repositories, carry their own dependencies and release on their
own schedule. [The authoring guide](https://helmut.hoffer-von-ankershoffen.me/heinzel/PLUGIN_AUTHORING/) covers
scaffolding, local preview, tests, builds, publication and catalog discovery.

For a minimal SDK without the standard plugins:

```sh
uv tool install heinzel
```

[Contribute](https://helmut.hoffer-von-ankershoffen.me/heinzel/guides/contribute/) ·
[Source](https://github.com/helmut-hoffer-von-ankershoffen/heinzel) ·
[Issues](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/issues) ·
[License](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/LICENSE)
