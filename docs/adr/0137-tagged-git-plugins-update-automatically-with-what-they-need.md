---
status: proposed
date: 2026-11-07
implementation: built
decision-makers: maintainer
amends: 0116
amends-0116: plugins pinned to a tagged GitHub commit update automatically, together with the plugins they need
related: 0094
---

# 0137: Tagged git plugins update automatically, with what they need

## Context and Problem Statement

[ADR 0116](0116-automatic-updates-are-scheduled-by-the-running-app-server.md) leaves every plugin from git to be
updated by hand. A catalog plugin is installed from git, pinned to one commit
(`name @ git+https://github.com/OWNER/REPO@<commit>#subdirectory=plugins/<name>`), so the automatic updater left
exactly the plugins people install most often where they were, without a word. Plugins have their own versions and
their own tags, `<distribution>/vX.Y.Z` ([ADR 0094](0094-plugins-own-features-and-independent-delivery.md)), and one
plugin can need a newer release of another. How do such plugins update as safely as the ones from PyPI, and how do
plugins that need each other move without one of them holding back hz or the rest?

## Decision Drivers

- The same safety as for PyPI: stable releases only, a minimum age, exact versions, verified from metadata, and
  everything put back on any failure.
- A git tag can move; what is installed must be the commit announced.
- One resolver run for everything that moves; nothing is ever run to learn what a plugin needs.
- ADR 0094 independence: one plugin that can't move holds back only the plugins that need it, never hz or others.

## Considered Options

- **Follow the release tags of plugins pinned to a GitHub commit, and move each with the plugins it needs** (chosen)
- Follow the default branch: moves on every commit, no version, nothing to age.
- Keep git plugins by hand (ADR 0116): the catalog's plugins never update by themselves.

## Decision Outcome

Chosen option: **follow the release tags**, because a tag names a version and a commit that can be aged, recorded
and checked again, exactly like hz's own release tag.

- **Announce.** A plugin pinned to a full commit of a GitHub repository is asked through the GitHub API (the gh login,
  or the plugin settings' token), in bounded requests: one listing of its tags `<distribution>/v`, then the commit
  the newest stable one names and its date. Pre-releases, development releases and other tag shapes are ignored. The
  release counts once it is as old as `auto_update_plugin_min_age_hours`, by the later of its commit date and, for an
  annotated tag, the tag's date. The commit is recorded with the news. The project table at that commit must name the
  tag's version. A failed check keeps the news from before, as for PyPI.
- **Apply.** `system.apply-announced` installs such a plugin at exactly the recorded commit, asks only it again
  (`--refresh-package`, `--reinstall-package`) and holds everything else as ADR 0116 does. Before anything is
  installed, its tag must still name the recorded commit, or nothing is installed and one message says so.
  Afterwards its metadata must name the announced version and its `direct_url.json` the recorded commit, or the tool
  folder is put back. The receipt names the new commit: the plugin stays pinned.
- **Plugins that need plugins.** What each installed plugin requires of another is read from its metadata
  (`Requires-Dist`); what a new release requires, from PyPI's release JSON or the project table at the tagged commit.
  A release that needs another plugin newer moves only together with that plugin's own release, in the same uv run,
  dependencies first in the news. If that release can't move (too new, held back, not released), the plugin that needs
  it waits too, and so does a release an installed plugin doesn't allow; one line in the log and in the Updates
  status (`plugins_held`) says so. Only that closure waits: hz and unrelated plugins update as before.
- Plugins from a git branch, another host, a folder or a URL, and git plugins whose repository has no such tags, stay
  updated by hand; the Updates pane names only those.

### Consequences

- Good, because catalog plugins update by themselves with the same checks as PyPI plugins and hz.
- Good, because plugins that need each other never land half updated, and never hold back hz.
- Bad, because a lightweight tag set today on an old commit counts as old as the commit: git records no date for it.
- Bad, because whether a new plugin release allows the new hz is still left to uv: a conflict puts everything back.

### Confirmation

Unit tests fake GitHub's answers: tag listing, a moved tag refused, a release too new, pre-releases ignored, a
dependency closure announced together, a dependent held when its dependency can't move, and hz and unrelated plugins
unaffected; the apply tests check the commit installed and verified.
