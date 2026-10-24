---
status: accepted
date: 2026-10-23
---

# 0007: Plugins are scaffolded with copier from this repository

## Context and Problem Statement

Every plugin repository needs the same standards as Heinzelmännchen: tooling, CI/CD, release process, tests, agent
instructions and reviewer agents. Copy-pasting drifts; a cookiecutter can't update existing projects.

## Considered Options

- **A copier template in `template/` of this repository** (chosen)
- **A separate template repository**: versions would drift from Heinzelmännchen's.
- **cookiecutter**: no update path.

## Decision Outcome

Chosen option: **a copier template in this repository, versioned by Heinzelmännchen's release tags**, because
plugins can follow Heinzelmännchen with `copier update`.

- `template/` in this repository is a [copier](https://copier.readthedocs.io) template; `copier.yml` at the root
  sets `_subdirectory: template` and `[[ ]]`/`[% %]` Jinja delimiters so GitHub Actions' `${{ }}` stays literal.
- Template versions are Heinzelmännchen's release tags, so a plugin scaffolded from `v0.3.0` declares compatibility
  with Heinzelmännchen 0.3 and can follow Heinzelmännchen with `copier update`.
- The template has no `_tasks`, so it never needs `--trust`.
- `hz system plugin new <dir>` runs `copier copy` for the user. `heinzel-plugin-example` is generated from the
  template and kept up to date with `copier update`.
- CI renders the template and runs the rendered plugin's lint and tests against the Heinzelmännchen checkout.

### Consequences

- Good: improving standards in Heinzelmännchen improves every plugin on its next `copier update`.
- Bad: template changes must keep the rendered project green.

### Confirmation

The CI job "Plugin template" runs `make template-test` (`scripts/template-test.sh`); `make lint` checks that
generated template files are in sync (`scripts/sync_template.py --check`).
`tests/unit/modules/plugin_service_test.py::test_new_pins_the_template_and_passes_answers` covers `plugin new`.
