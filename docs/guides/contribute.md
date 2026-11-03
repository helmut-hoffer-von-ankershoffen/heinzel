---
title: Find your contribution path
description: Build a plugin, design a theme, improve the docs, or contribute to the Heinzelmännchen core.
---

# Build something useful.

Start where you can make a small, verifiable improvement. A plugin is often the fastest route: it lives in your
repository, carries its own dependencies, and tests against an installed SDK rather than all of core.

| What you want to make | Your path | Your feedback loop |
| --- | --- | --- |
| A tool integration, operation, widget or notification | [Custom plugin guide](../PLUGIN_AUTHORING.md) | `make check-fast`, owning checks, wheel acceptance |
| A visual identity and soundscapes | [Theme guide](../THEMES.md) | Theme plugin preview and owning tests |
| A clearer explanation or example | [Documentation sources](../README.md#documentation-map) | `make docs-site` offline; browser checks for site changes |
| A generic capability or extension contract | [Core contribution guide](../../CONTRIBUTING.md) | Core checks and an ADR for a new architectural decision |
| A reproducible bug report | [GitHub issues](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/issues) | Expected result, actual result, version, minimal fictional example |

## The short plugin path

```sh
hz system plugin new heinzel-harbor --defaults --data plugin_name=harbor --data github_owner=your-account
cd heinzel-harbor
make setup
make check-fast
```

The generated README, AGENTS.md and CLAUDE.md explain the project to humans and coding agents. The minimal
preset includes an operation and test; the `contributions` preset demonstrates settings, services, navigation,
a Today widget, notifications and a local view. Follow the [complete author guide](../PLUGIN_AUTHORING.md)
through dependencies, public or private hosting, testing, building and publishing.

## A few design rules that keep us fast

- Core depends on public contracts. It never imports a concrete plugin implementation.
- Plugins may implement shared interfaces; consumers depend on the interface rather than the provider.
- A plugin owns its behavior, dependencies, assets, documentation, tests and version.
- Add capabilities through extension points before creating a private integration path.
- Review a concrete change and its evidence. Use fictional data in public examples.

## Improve this site

```sh
make setup
make docs-site
make docs-site-check
```

The ordinary build uses the checked-in public catalog without a network. `make docs-catalog` explicitly refreshes
metadata when GitHub CLI is available. Publishing refreshes it automatically. See
[catalog metadata and automation](catalog.md) for the boundary and test contract.

Fixing a typo or an unclear sentence in these docs is a good first contribution. Small changes like that have a quick path of their own: ask your coding agent to use the ship small skill.
