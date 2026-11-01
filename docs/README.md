---
title: Heinzelmännchen — helpers for good work
description: Heinzelmännchen, or Heinzel for short, is an open-source crew of AI helpers that draft your busywork. You approve; it runs on your machine.
template: home.html
hide:
  - navigation
  - toc
---

<div class="hz-hero" markdown>
<div class="hz-hero-copy" markdown>
<p class="hz-eyebrow">Open source. Runs on your machine.</p>

# Less busywork.<br>More good work.

<p class="hz-lead">Heinzelmännchen are small AI helpers that draft the busywork nobody chose. They work with your own accounts. You approve everything.</p>

<div class="hz-actions" markdown>
[Install Heinzel ↗](guides/install.md){ .hz-button }
[Explore plugins](CATALOG.md){ .hz-button .hz-button-quiet }
</div>
<p class="hz-caption">MIT licensed · macOS and Linux · CLI, MCP, REST and a web workspace</p>

</div>
<div class="hz-hero-art">
<picture><source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg"><img src="assets/hero-light.svg" alt="A small crowd of Heinzelmännchen helpers walking together" width="1000" height="360"></picture>
<div class="hz-art-note"><span class="hz-dot"></span> A crew, not a console.</div>
</div>
</div>

<section class="hz-trust" aria-labelledby="hz-trust-title">
<h2 id="hz-trust-title">Open code. Visible checks.</h2>
<p>Check the license, practices and analysis before you install.</p>
<div class="hz-trust-links">
<a href="https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-brightgreen" alt="MIT license" height="20" loading="lazy"></a>
<a href="https://www.bestpractices.dev/en/projects/15863"><img src="https://img.shields.io/cii/level/15863?label=OpenSSF%20Best%20Practices" alt="OpenSSF Best Practices: view the project's criteria" height="20" loading="lazy"></a>
<a href="https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/codeql.yml?query=branch%3Amain"><img src="https://img.shields.io/github/actions/workflow/status/helmut-hoffer-von-ankershoffen/heinzel/codeql.yml?branch=main&amp;label=CodeQL" alt="CodeQL analysis on main: view the workflow runs" height="20" loading="lazy"></a>
<a href="https://sonarcloud.io/summary/new_code?id=helmut-hoffer-von-ankershoffen_heinzel"><img src="https://img.shields.io/sonar/quality_gate/helmut-hoffer-von-ankershoffen_heinzel?server=https://sonarcloud.io&amp;label=Quality%20Gate%20Status" alt="Quality gate: new core code" height="20" loading="lazy"></a>
<a href="https://sonarcloud.io/summary/overall?id=helmut-hoffer-von-ankershoffen_heinzel"><img src="https://sonarcloud.io/api/project_badges/measure?project=helmut-hoffer-von-ankershoffen_heinzel&amp;metric=security_rating" alt="Security rating: overall core code" height="20" loading="lazy"></a>
</div>
<p class="hz-trust-note">The gate checks new code; the security rating covers all analyzed core code.</p>
</section>

<div class="hz-strip"><span>You keep the decisions.</span><span>Helpers do the drafting.</span><span>Your accounts, your machine.</span></div>

<section class="hz-section" markdown>
<p class="hz-eyebrow">One foundation. Many ways in.</p>

## Work where you work.

<div class="hz-feature-grid" markdown>
<div class="hz-feature" markdown>
<span class="hz-number">01 / WORKSPACE</span>

### See the work, together.

Each helper shows what it is doing, in words and with a face. When it waits for you, it says so.

[Take your first steps →](guides/first-steps.md)
</div>
<div class="hz-feature" markdown>
<span class="hz-number">02 / AUTOMATION</span>

### Write once. Reach every head.

One Python operation becomes a CLI command, an MCP tool and a REST endpoint.

[Tour the architecture →](guides/architecture.md)
</div>
<div class="hz-feature" markdown>
<span class="hz-number">03 / EXTENSION</span>

### Make room for your tools.

Plugins add operations, views and notifications. Themes add colors, art and sound. Each ships on its own.

[Create your first plugin →](PLUGIN_AUTHORING.md)
</div>
</div>
</section>

<section class="hz-section" markdown>
<p class="hz-eyebrow">Meet your next few helpers</p>

## Small jobs. A little time back.

Imagined helpers. Real screens, made-up data. Select an image to zoom.

<!-- hz:home-gallery -->

[Try Heinzel →](guides/install.md){ .hz-button }
[Take the product tour](guides/tour.md){ .hz-button .hz-button-quiet }
</section>

<section class="hz-section" markdown>
<p class="hz-eyebrow">Your workspace, your way</p>

## A small core. A growing world.

Every entry shows its source, compatibility and exact revision.

<!-- hz:featured -->

[Explore all plugins and themes ↗](CATALOG.md){ .hz-button .hz-button-quiet }
</section>

<section class="hz-section hz-manifesto" markdown>
<p class="hz-eyebrow">The direction is human</p>

## Let helpers do the drafting.<br>Let people make the decisions.

The goal: a crew of durable helpers that take over any busywork you can automate, while people approve. Today you get the foundation: kernel, CLI, web, MCP and REST, local drafts on a schedule, and plugins. Helpers working together across a team are not built yet.

[Read the vision →](VISION.md) · [Inspect the decisions →](adr/README.md)
</section>

<section class="hz-section" markdown>
<p class="hz-eyebrow">Build something useful</p>

## Your first contribution can be a plugin.

Scaffold a repository, write one operation, test it. Your plugin has its own dependencies and releases.

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
| **Extend** it with modules and plugins | [Plugin extension guide](PLUGINS.md) · [README: Extend](../README.md#extend) · [ARCHITECTURE: Plugins](ARCHITECTURE.md#plugins) · [writing a playbook](PLAYBOOKS.md#writing-a-playbook) · [themes, and how to make a weather theme](THEMES.md) · [settings that need no explaining](SETTINGS_UX.md) · [screenshot playbook](SCREENSHOT_PLAYBOOK.md) · the template's AGENTS.md (`hz system plugin new`) |
| **Publish and verify a release** | [Publishing acceptance](PUBLISHING.md) |
| **Contribute** to the core | [CONTRIBUTING](../CONTRIBUTING.md) · [CODING_STANDARDS](CODING_STANDARDS.md) · [Sonar security lessons](SONAR_SECURITY.md) · [docs style check](dev/DOCS_CHECK.md) · [durable session storage](SESSIONS.md) · [shipping small changes with a small model](SHIP_SMALL.md) · [CHANGELOG](../CHANGELOG.md) *(generated at release)* |
| Work on it **as a coding agent** | [AGENTS](../AGENTS.md) and the scoped AGENTS.md files it lists |
| Report a **vulnerability** | [SECURITY](../SECURITY.md) |
| Read the **legal notices** of the website (German texts prevail) | [Impressum](impressum.md) · [Imprint](imprint.md) · [Datenschutzerklärung](datenschutz.md) · [Privacy notice](privacy.md) |

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


Site guides: [Install](guides/install.md) · [First steps](guides/first-steps.md) · [Product tour](guides/tour.md) · [Contribute](guides/contribute.md) · [Architecture tour](guides/architecture.md) · [Catalog metadata and automation](guides/catalog.md) · [Vision](VISION.md) · [Catalog](CATALOG.md).

</details>
