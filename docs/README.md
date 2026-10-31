---
title: Heinzelmännchen — helpers for good work
description: An open-source, local-first foundation for product teams and their AI helpers. Install Heinzel, explore plugins, or build your own.
template: home.html
hide:
  - navigation
  - toc
---

<div class="hz-hero" markdown>
<div class="hz-hero-copy" markdown>
<p class="hz-eyebrow">Open source. Local first. Yours to extend.</p>

# Less busywork.<br>More good work.

<p class="hz-lead">Meet Heinzelmännchen: little helpers for product teams, with a serious foundation. Connect your tools, put repeatable work into workflows, and keep people in control.</p>

<div class="hz-actions" markdown>
[Install Heinzel ↗](guides/install.md){ .hz-button }
[Explore plugins](CATALOG.md){ .hz-button .hz-button-quiet }
</div>
<p class="hz-caption">MIT licensed · macOS and Linux · CLI, MCP, REST and a web workspace</p>

</div>
<div class="hz-hero-art">
<picture><source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg"><img src="assets/hero-light.svg" alt="A small crowd of Heinzelmännchen helpers walking together" width="1000" height="360"></picture>
<div class="hz-art-note"><span class="hz-dot"></span> Small helpers. Shared purpose.</div>
</div>
</div>

<div class="hz-strip"><span>For people who build products.</span><span>For agents that help them.</span><span>For teams that want control.</span></div>

<section class="hz-section" markdown>
<p class="hz-eyebrow">One foundation. Many ways in.</p>

## Work where you work.

<div class="hz-feature-grid" markdown>
<div class="hz-feature" markdown>
<span class="hz-number">01 / WORKSPACE</span>

### See the work, together.

A web workspace brings Today, contracts and team context into view. Heinzelmännchen show their state in words and character, so waiting for a person is visible.

[Take your first steps →](guides/first-steps.md)
</div>
<div class="hz-feature" markdown>
<span class="hz-number">02 / AUTOMATION</span>

### Write once. Reach every head.

A typed Python operation can supply a CLI command, an MCP tool and a REST endpoint. Privacy and head restrictions govern access from terminals, agents and applications.

[Tour the architecture →](guides/architecture.md)
</div>
<div class="hz-feature" markdown>
<span class="hz-number">03 / EXTENSION</span>

### Make room for your tools.

Plugins own their operations, views, widgets and notifications. Themes bring palettes, artwork and soundscapes. Build, test and release each independently.

[Create your first plugin →](PLUGIN_AUTHORING.md)
</div>
</div>
</section>

<section class="hz-section" markdown>
<p class="hz-eyebrow">Meet your next few helpers</p>

## Small jobs. A little time back.

Imagined helpers. Real product screens with fictional demonstration data. Select an image to zoom.

<!-- hz:home-gallery -->

[Try Heinzel →](guides/install.md){ .hz-button }
</section>

<section class="hz-section" markdown>
<p class="hz-eyebrow">Your workspace, your way</p>

## A small core. A growing world.

Discover capabilities without editing core. Every catalog entry shows its source, compatibility declaration and immutable revision.

<!-- hz:featured -->

[Explore all plugins and themes ↗](CATALOG.md){ .hz-button .hz-button-quiet }
</section>

<section class="hz-section hz-manifesto" markdown>
<p class="hz-eyebrow">The direction is human</p>

## Let helpers do the drafting.<br>Let people make the decisions.

The vision is an open-source AI operating system for product teams: model agnostic, composable and accountable. Today’s release supplies the kernel, heads, workflows and extension foundation. End-to-end team automation is a journey we are building in the open.

[Read the vision →](VISION.md) · [Inspect the decisions →](adr/README.md)
</section>

<section class="hz-section" markdown>
<p class="hz-eyebrow">Build something useful</p>

## Your first contribution can be a plugin.

Scaffold a public or private repository, write one operation, and test it against an installed SDK. Your plugin can carry its own dependencies and release on its own schedule.

```sh
hz system plugin new heinzel-harbor --defaults --data plugin_name=harbor --data github_owner=your-account
cd heinzel-harbor
make setup
make check-fast
```

<div class="hz-actions" markdown>
[Build a plugin ↗](PLUGIN_AUTHORING.md){ .hz-button }
[Find your contribution path](guides/contribute.md){ .hz-button .hz-button-quiet }
</div>
</section>

<details class="hz-documentation-map" markdown>
<summary>Browse every guide and reference</summary>

## Documentation map

Build a custom plugin in its own public or private repository with the [plugin author guide](PLUGIN_AUTHORING.md).

Find what you need by who you are. Files marked *generated* are written by `make docs` from the code, and CI fails when
they are out of date, so they can't drift.

| You want to… | Read |
| --- | --- |
| **Get started**: bootstrap a team as its manager, or join one with the line your manager posted | [guides/manager.md](guides/manager.md) · [guides/joining.md](guides/joining.md) |
| **Use** Heinzelmännchen: install, commands, configuration | [README](../README.md) · [MODULES](MODULES.md) *(generated)* · [CLI_REFERENCE](CLI_REFERENCE.md) *(generated)* · [CONFIGURATION](CONFIGURATION.md) *(generated)* |
| **Your Heinzelmännchen**: the journey from nothing to a scheduled team contract, and the contract reference | [HEINZELMAENNCHEN](HEINZELMAENNCHEN.md) (was [WORKFLOWS](WORKFLOWS.md)) · [playbooks: your first contract from a plugin](PLAYBOOKS.md) |
| **Work as a team**: create, join and add to the team repository | [TEAMS](TEAMS.md) |
| **Build durable turns**: typed outcomes, checkpoints and offline engine tests | [DURABLE_TURNS](DURABLE_TURNS.md) |
| **Build the desktop lifecycle foundation** | [Shared runtime authority](DESKTOP_RUNTIME_AUTHORITY.md) · [Production gates still open](DESKTOP_PRODUCTION_GATES.md) |
| **Understand** why it exists and how it is built | [INTENT](../INTENT.md) · [ARCHITECTURE](ARCHITECTURE.md) · [decisions (ADRs)](adr/README.md) · research: [daemon head](research/daemon-head.md) · [workflow contracts](research/workflow-contracts.md) · [embedded workflows](research/embedded-workflows.md) · [inventory](research/inventory.md) · [people and org](research/people-and-org.md) · [onboarding](research/onboarding.md) · [plugin extraction opportunities](research/plugin-extraction-opportunities.md) |
| **Pick a plugin**: the gallery, one page per plugin with what it brings in every head, its settings, themes and recordings | [Public plugin catalog](CATALOG.md), with immutable source revisions and theme previews · `hz system plugin search` discovers available plugins automatically |
| **Extend** it with modules and plugins | [Plugin extension guide](PLUGINS.md) · [README: Extend](../README.md#extend) · [ARCHITECTURE: Plugins](ARCHITECTURE.md#plugins) · [writing a playbook](PLAYBOOKS.md#writing-a-playbook) · [themes, and how to make a weather theme](THEMES.md) · [settings that need no explaining](SETTINGS_UX.md) · the template's AGENTS.md (`hz system plugin new`) |
| **Publish and verify a release** | [Publishing acceptance](PUBLISHING.md) |
| **Contribute** to the core | [CONTRIBUTING](../CONTRIBUTING.md) · [CODING_STANDARDS](CODING_STANDARDS.md) · [Sonar security lessons](SONAR_SECURITY.md) · [durable session storage](SESSIONS.md) · [CHANGELOG](../CHANGELOG.md) *(generated at release)* |
| Work on it **as a coding agent** | [AGENTS](../AGENTS.md) and the scoped AGENTS.md files it lists |
| Report a **vulnerability** | [SECURITY](../SECURITY.md) |

How drift is prevented:

- Everything that can be derived from the code is generated: the module catalog, the CLI and configuration
  references, the README's module list, every plugin's page (`hz system plugin docs build`, ADR 0082), the changelog
  and the release notes.
- What can't be generated is checked: every operation needs a docstring and every parameter and setting a
  description (they become help text in every head), every relative link must resolve, and every document in `docs/`
  must be listed here.
- The README's install, use and uninstall instructions are run as tests on macOS and Linux.
- The `docs-reviewer` agent reviews prose against the code in every pull request that changes behaviour.

- [Private HTTPS web sharing](PRIVATE_WEB_SHARING.md): exact proxy origins, pairing and terminal security.


Site guides: [Install](guides/install.md) · [First steps](guides/first-steps.md) · [Contribute](guides/contribute.md) · [Architecture tour](guides/architecture.md) · [Catalog metadata and automation](guides/catalog.md) · [Vision](VISION.md) · [Catalog](CATALOG.md).

Legacy generated recordings (historical; current sources are in the catalog): [Atlassian](plugins/heinzel-atlassian/index.md) · [Clock](plugins/heinzel-clock/index.md) · [GitHub](plugins/heinzel-github/index.md) · [Google](plugins/heinzel-google/index.md) · [Tailscale](plugins/heinzel-tailscale/index.md) · [Zoom](plugins/heinzel-zoom/index.md).

</details>
