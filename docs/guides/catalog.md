---
title: Showcase your plugin
description: Automatic public discovery, catalog metadata, safe theme artwork, and offline website development.
---

# From your repository to the catalog.

A custom plugin can live in any public or private repository. See the [end-to-end author guide](../PLUGIN_AUTHORING.md)
for scaffolding, dependencies, tests, builds and publishing. This page covers its public showcase.

## Automatic public discovery

The public site reads the core, contribution and plugin collection source locations, plus public GitHub repositories
with the `heinzel-plugin` topic. It recognizes a project at the repository root or `plugins/<folder>/pyproject.toml`
by its `project.entry-points."heinzel.modules"` declaration. It reads metadata at an immutable commit; it never
imports, installs or executes the plugin to build the website.

```sh
gh repo edit your-account/heinzel-harbor --add-topic heinzel-plugin
```

A new public project appears after a successful scheduled refresh. GitHub schedules are best effort; the catalog
shows its snapshot time. A broken or over-budget optional source is omitted with a generic notice. Per-source project and blob limits,
an aggregate project cap and a refresh API budget keep one topic repository from crowding out healthy entries. An incomplete discovery or
required-source failure keeps the previously deployed site. Publication uses a freshly checked public visibility
for every included source. Removal or visibility changes take effect on the next successful refresh.

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

Theme previews read actual light and dark palette tokens from the declared TOML. For existing projects, theme
TOML inside an explicitly declared Hatch wheel package's `themes/` directory is also recognized. The site draws
its own inert illustration; it labels it a palette preview rather than presenting it as a runtime screenshot.

The "included" badge is derived from the official standard profile and owning metadata. Declaring yourself
standard in another repository does not grant that badge. Source versions and compatibility declarations are
shown as metadata, not as a certification against the newest SDK.

## Build and verify the website

```sh
make docs-site          # deterministic build from checked-in public metadata
make docs-site SERVE=1  # local preview with reload
make docs-site-check    # browser behavior, phone layouts, light/dark and accessibility
make docs-catalog       # explicit online refresh; needs gh authentication
```

`docs/_data/catalog.json` is the offline snapshot. `HZ_SITE_CATALOG=/path/to/catalog.json` selects another snapshot
for a site build. Unit tests use synthetic unknown projects and hostile metadata; browser checks exercise the
built site, including keyboard filtering and a JavaScript-disabled catalog.

The Docs workflow refreshes public metadata on an hourly schedule, relevant changes to `main`, manual dispatch
and release publication. It checks out current `main` for publication so an older release cannot roll back site
code. One production deployment queue serializes publication. Provider media recording is a separate explicit
job and is never part of metadata refresh. No cross-repository publishing credential is required.

The installation CTA advances only when a complete standard profile, matching release artifact hashes and the
published core PyPI wheel can be verified. See [ADR 0098](../adr/0098-pages-showcases-the-product-and-discovers-public-plugin-metadata.md)
for the publishing boundary.
