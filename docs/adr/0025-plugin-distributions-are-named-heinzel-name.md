---
status: accepted
date: 2026-10-23
implementation: built
decision-makers: maintainer
amends: 0016
amends-0016: plugin naming
related: 0007, 0017
---

# 0025: Plugin distributions are named `heinzel-<name>`

*Amended by 0094: first-party plugins now live in the heinzel-plugins repository.*

## Context and Problem Statement

[ADR 0016](0016-name-heinzelmaennchen.md) named plugins `heinzel-plugin-*`. [ADR 0017](0017-first-party-plugins-in-the-core-repository.md)
then named first-party plugins `heinzel-<name>` (`heinzel-clock`, `heinzel-atlassian`), so the repository had two
conventions: one for plugins in `plugins/` and one for plugins scaffolded from `template/`. A plugin promoted from
its own repository into `plugins/` would have to be renamed, which breaks every install URL and receipt that names
it. Which name does a plugin distribution get?

## Decision Drivers

- One convention for every plugin, so promoting an external plugin into `plugins/` changes nothing for its users.
- Short names: distributions, repositories and `hz system plugin remove <distribution>` are typed.
- The name a user types (`hz system plugin add clock`, `hz clock ...`) maps to the distribution without a lookup.

## Considered Options

- **`heinzel-<name>` for every plugin, first-party or external** (chosen)
- **Keep `heinzel-plugin-<name>` for external plugins and `heinzel-<name>` for first-party ones**: two conventions,
  and promotion renames the distribution.
- **`heinzel-plugin-<name>` for every plugin**: longer, and the first-party packages and extras (`heinzel[atlassian]`)
  would no longer match their distributions.

## Decision Outcome

Chosen option: **every plugin distribution is named `heinzel-<name>`**, because one convention serves first-party and
external plugins alike and promotion becomes a move, not a rename.

- Distribution and repository: `heinzel-<name>`, first-party or external.
- Root package: `heinzel_<name>`.
- Entry point: `<name> = "heinzel_<name>"` in the `heinzel.modules` group; `<name>` is also the command group
  (`hz <name> ...`).
- `hz system plugin add <name>` resolves a first-party name to `plugins/heinzel-<name>` at the installed core
  version (ADR 0017); anything else is a uv spec (git URL, path, package name).
- The copier template defaults to `heinzel-<name>` / `heinzel_<name>`; the example plugin is `heinzel-example`.
- This replaces the sentence "Plugins are `heinzel-plugin-*`" of ADR 0016; the rest of ADR 0016 stands.

### Consequences

- Good: one convention; `plugins/heinzel-<name>`, `heinzel_<name>`, `hz <name>` and the extra `heinzel[<name>]` line
  up.
- Bad: breaking for plugin authors who scaffolded with the old defaults. They rename the distribution and package,
  or keep the old name until their next major release; discovery reads the entry point group, not the name, so old
  plugins still load.
- Bad: external and first-party plugins share one namespace, so an external plugin can collide with a later
  first-party name.

### Confirmation

- `tests/fixtures/heinzel-dummy` and `make template-test` (renders `heinzel-example` from the template) exercise the
  convention end to end.
- `tests/unit/repository_test.py::test_first_party_plugins_match_the_plugins_directory` keeps first-party names
  equal to `plugins/heinzel-<name>`.
- Review: the copier defaults in `copier.yml` and the docs.

## More Information

- [ADR 0007](0007-plugins-scaffolded-with-copier.md) calls the example plugin `heinzel-plugin-example`; it is
  `heinzel-example` (github.com/helmut-hoffer-von-ankershoffen/heinzel-example) from now on. Since 2026-10-24 it
  lives in heinzel-contrib, the repository of community plugins, as `plugins/heinzel-example`; its own repository
  is archived.
- Open questions: who keeps a name when an external plugin collides with a later first-party one; whether the PyPI
  keyword `heinzel-plugin` in the template stays as the way to find plugins.
