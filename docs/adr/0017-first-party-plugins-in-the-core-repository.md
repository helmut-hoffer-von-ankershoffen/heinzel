---
amended-by: 0094
status: accepted
date: 2026-10-23
---

# 0017: First-party plugins live in the core repository as workspace packages

*Amended by 0094: first-party plugins now live in the heinzel-plugins repository.*

## Context and Problem Statement

Integrations (Atlassian, Google Workspace, Slack, …) are plugins: optional, with their own dependencies. Many of them
are maintained by the core team and should move in lockstep with the kernel; others belong to companies, sites or
teams and live in their own repositories.

## Considered Options

- **First-party plugins as uv workspace members in `plugins/`, released with the core** (chosen)
- **Every plugin in its own repository**: kept for external plugins, which are scaffolded from `template/`.

No other alternatives were recorded when this decision was made.

## Decision Outcome

Chosen option: **first-party plugins live in `plugins/` of this repository as workspace packages**, because they
move in lockstep with the kernel while users only get the dependencies they need.

- First-party plugins live in `plugins/<distribution>/` of this repository as uv workspace members: separate
  distributions (`heinzel-atlassian`, `heinzel-google`, …) with their own `pyproject.toml` and `heinzel.modules`
  entry point, held to the same import contracts as external plugins (public API only).
- They are versioned, tested and released together with the core: same tag, same changelog, same CI and reviewers.
- Installing one is short: `hz system plugin add atlassian` resolves a first-party name to the package at the
  installed core version; extras (`heinzel[atlassian]`) work too. Users only get the dependencies they need.
- External plugins live in their own repositories, scaffolded from `template/`. A proven external plugin can be
  promoted into `plugins/` unchanged, because it already has the same shape.
- `plugins/` holds the first-party plugins and nothing else, to keep "plugin" unambiguous
  (see [ADR 0018](0018-one-administrative-namespace.md) for `agent-skills/`). The directory is named
  `plugins/` because it contains plugins.

### Consequences

- Bad: the release tooling builds, versions and publishes several distributions from one tag.
- Neutral: implemented with the first integration, so the tooling is shaped by a real package.

### Confirmation

- import-linter contract "First-party plugins use only the public API, like any plugin" in `pyproject.toml`.
- `tests/unit/repository_test.py::test_first_party_plugins_match_the_plugins_directory` keeps `FIRST_PARTY` in
  `src/heinzel/modules/system/plugin/_service.py` equal to `plugins/`; `tests/unit/modules/plugin_service_test.py`
  covers resolving first-party names.
- CI and `publish.yml` build every distribution with `uv build --all-packages`.

## More Information

The record was proposed with "implemented with the first integration (Atlassian), not before". It was implemented
with the clock plugin (`plugins/heinzel-clock`) instead and accepted on 2026-10-23.
