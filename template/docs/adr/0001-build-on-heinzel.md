---
status: accepted
date: 2026-10-23
---

# 0001: Build on Heinzelmännchen's kernel and standards

## Context and Problem Statement

This plugin extends Heinzelmännchen. Heinzelmännchen defines how modules, operations, services and heads work, and
the standards every repository in its ecosystem follows.

## Considered Options

- **Build on Heinzelmännchen's public API and its copier template** (chosen)

No alternatives were recorded when this decision was made.

## Decision Outcome

Chosen option: **build on Heinzelmännchen's kernel and standards**, because the plugin then works in every head and
keeps up with shared standards.

- The plugin is a set of Heinzelmännchen modules discovered through the `heinzel.modules` entry point; it uses only
  Heinzelmännchen's public API.
- Tooling, CI/CD, coding standards, reviewer agents and hooks come from Heinzelmännchen's copier template and are
  kept current with `copier update`.

### Consequences

- Good: operations of this plugin appear in every Heinzelmännchen head without extra work.
- Neutral: changes to shared standards are made in Heinzelmännchen's template, not here.

### Confirmation

`copier update` keeps the template's files current; review checks that only Heinzelmännchen's public API is used.
