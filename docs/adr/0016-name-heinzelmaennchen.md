---
status: accepted
date: 2026-10-23
amended-by: 0025
---

# 0016: The project is called Heinzelmännchen; the command is `hz`

## Context and Problem Statement

The working title was Joy. `joy` is taken on PyPI, and the project's character became clearer: composable,
team-centric, mostly without a user interface, working in the background. The command is typed constantly, so it
must be short.

## Considered Options

- **Heinzelmännchen, with the command `hz` and the distribution `heinzel`** (chosen)
- **Keep the working title Joy**: `joy` is taken on PyPI.

## Decision Outcome

Chosen option: **the brand is Heinzelmännchen, the command is `hz`, the distribution is `heinzel`**, because the
name fits the project's character and both short names are available where they are needed.

- Brand: **Heinzelmännchen**, after the house spirits of Cologne who finished the townspeople's work at night. Logo: 🧝,
  where only text can go; the image is an original drawing of a friendly Heinzelmännchen with a red cap (`heinzel.svg`;
  a swarm of them in the logo, ADR 0046).
- Command: **`hz`**. Everything a user types or sees uses `hz`: environment variables `HZ_*`, the `[hz]` config
  table, the project file `hz.toml`, the configuration directory `hz`, the skills `/hz-init` and `/hz-uninstall`.
- Distribution, repository and Python package: **`heinzel`** (free on PyPI; `hz` is taken). Plugins are
  `heinzel-plugin-*` and register in the `heinzel.modules` entry point group.

### Consequences

- Bad: one rename before the first release; afterwards names live in tags, install URLs and plugins and are
  expensive to change.
- Neutral: the tenet "Joyful" stays: it describes what we build, not what it is called.

### Confirmation

Review only; no automated check. The names are declared in `pyproject.toml` (distribution, `hz` script,
`heinzel.modules` entry point group) and exercised by every CLI test.
