---
name: update-template
description: Update this plugin to the latest Heinzelmännchen copier template, bringing in Heinzelmännchen's current standards, tooling, CI, agents and hooks. Use when Heinzelmännchen released a new version, when asked to update or sync with Heinzelmännchen, or when shared tooling is out of date.
---

# Update from the Heinzelmännchen template

1. Start from a clean working tree on a new branch.
2. `make update-template` (runs `uvx copier update --defaults`). Copier re-renders the template at the old and new
   Heinzelmännchen tags and applies the difference.
3. Resolve conflicts (inline markers by default); keep your plugin's code, take the template's tooling.
4. `uv lock` (the template moves the Heinzelmännchen dependency forward), then `make check` and `make test-e2e`.
5. Commit as `chore(template): update to heinzel vX.Y.Z` and open a pull request.

Never hand-edit `.copier-answers.yml`.
