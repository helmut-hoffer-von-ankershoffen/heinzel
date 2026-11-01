---
title: Showcase your plugin
description: Automatic public discovery, catalog metadata, screenshots and soundscapes for your plugin.
---

# From your repository to the catalog.

A custom plugin can live in any public or private repository. See the [end-to-end author guide](../PLUGIN_AUTHORING.md)
for scaffolding, dependencies, tests, builds and publishing. This page covers its public showcase.

## Automatic public discovery

The public site reads the core and plugin collection source locations, plus public GitHub repositories
with the `heinzel-plugin` topic. It recognizes a project at the repository root or `plugins/<folder>/pyproject.toml`
by its `project.entry-points."heinzel.modules"` declaration. It reads metadata at an immutable commit; it never
imports, installs or executes the plugin to build the website.

```sh
gh repo edit your-account/heinzel-harbor --add-topic heinzel-plugin
```

A new public project appears after the next scheduled refresh, and the catalog shows its snapshot time. Removing a project or making it private takes effect on the next refresh. How the pipeline works: [site pipeline](../PUBLISHING.md#website-catalog-pipeline).

Private projects are excluded even when the workflow's GitHub account can read them. They remain discoverable
through authenticated CLI catalog access for authorized users. There is no public page or private metadata cache
on this website.

## Add a good description

The ordinary project name, version, description and Heinzel dependency range already supply a useful entry.
Add optional owner-maintained presentation metadata to the same `pyproject.toml`:

```toml
[tool.heinzel.catalog]
title = "Harbor"
category = "utilities"
highlights = [
  "Summarize the work waiting for you",
  "One operation through CLI, MCP and REST",
  "A Today widget and useful notifications",
]
# Paths are relative to this project, including in a collection.
artwork = ["docs/assets/harbor.webp"]
themes = ["src/heinzel_harbor/themes/harbor.toml"]
```

Use one of `utilities`, `connectors`, `themes` or `reference`. Keep highlights factual and short. Omit `artwork`
and `themes` if your plugin does not have them. The site bounds text and file sizes, rejects traversal and links,
and accepts owner artwork as PNG, JPEG or WebP only. Supplier HTML, CSS, scripts and SVG do not enter the site.

Theme previews are inert illustrations drawn from your declared palette tokens.

## Publish UI and CLI screenshots

Every plugin detail page has a screenshot gallery with separate **User interface** and **Command line** groups.
This includes plugins that supply themes. Capture the running plugin and its CLI in an isolated demonstration
profile, commit the raster images to its owning public project, and declare them alongside its other metadata:

```toml
[[tool.heinzel.catalog.screenshots]]
path = "docs/assets/ui-light.webp"
surface = "ui"
caption = "Harbor in the app, light appearance"

[[tool.heinzel.catalog.screenshots]]
path = "docs/assets/cli.webp"
surface = "cli"
caption = "hz harbor status in the terminal"
```

Use at most eight PNG, JPEG or WebP screenshots, each no larger than 2 MiB. Paths are relative to the plugin,
including collection projects. Captions also serve as alternative text. Include light and dark captures for themes. Keep examples free of personal data and credentials.

## Showcase YouTube soundscapes

The catalog reads inert `*.toml` files under a declared Hatch wheel package's `soundscapes/` directory. With Hatch's
default layout it also recognizes `src/<distribution_with_underscores>/soundscapes/`. For a custom layout, declare
paths explicitly:

```toml
[tool.heinzel.catalog]
soundscapes = ["src/heinzel_harbor/soundscapes/tide.toml"]
```

It displays soundscapes declaring `kind = "stream"`, `[stream] provider = "youtube"` and an eleven-character
`video` identity. A **Listen on YouTube** link works without JavaScript;
**Play** loads the privacy-enhanced YouTube player only after a click. No video, thumbnail or player is requested
before that click. YouTube and the video owner control availability.
