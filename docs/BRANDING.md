# Branding

This page fixes the names, taglines and claims of Heinzelmännchen. `tests/unit/branding_test.py` keeps the key strings aligned.

## The name has five forms, each with one job

- **Heinzelmännchen** is the product. Use it in prose, titles and the web app.
- **`hz`** is the command. **`heinzel`** is the distribution on PyPI and the Python import.
- **heinzelmaennchen** is the ASCII form for identifiers, bundle names and package slugs. Do not use any other ASCII spelling.
- **a heinzel, heinzels** is the short form for one agent or several: "Adi is a heinzel who focuses on getting weekly team status reports drafted and approved." Lowercase in running text, capitalised at a sentence start. It is never the product name, so "Install Heinzel" is wrong.
- **helper** stays the plain word where it reads better.
- Never write that a named heinzel is an agent, an AI or a bot. Describe it by name and job.
- **Heinzel Notifier** keeps its capital. It is the macOS notification app.

## Three sentences, each with a place

- **Tagline:** "Less busywork. More good work." It is the hero on the Pages home and the opening of `PYPI.md`.
- **Descriptor:** "helpers accelerating product teams". It is line 2 of the CLI banner and the web manifest description (ADR 0071).
- **Description:** "Heinzelmännchen is an open-source crew of AI helpers that drafts your team's busywork and asks before anything leaves your machine."
- The description is the one-liner for `README.md`, `PYPI.md`, the home page description, the plugin marketplace and the MCP server.
- Where the field already names the project (`pyproject.toml`, `site_description`), drop the leading "Heinzelmännchen is".

## We do not claim what we have not shown

- We do not yet say autonomous, automatically, while you sleep, sends, publishes, delivers, secure or reliable.
- No run has been proven unattended with a live provider. No helper writes into a shared document.
- A claim needs a receipt beside it: a test, a release note or a recording that shows it.
- Helpers draft, and a person approves. Write "drafts" where the product does not yet do more.

## ACME is the fictional company

- **ACME**, all caps, is the display name of the made-up company in prose and UI text.
- **`acme`** is the identifier in commands, package names and config, such as `acme-example` on GitHub.
- Mixed case is not used. Class names such as `AcmeService` follow code style. ADRs and research keep their history.

## Three guides, and only three

- **Pip** is young and slim, with a blue hat and a teal shirt. Pip guides time and first steps.
- **Ada** is grown, with a teal hat and a rose shirt. Ada guides inputs, connections and drafts.
- **Otto** is old, with a slate hat, an ochre shirt and a long beard. Otto guides decisions, review and reference.
- The art lives in `scripts/site_characters.mjs`. Alt text must describe these looks.

## Terminal art is a garnish

- The bust appears in bare `hz` and `hz --version`. The swarm appears in `hz setup`.
- A walking glyph shows during work, and a finish glyph shows when it ends.
- `hz --help` has no art, by design.
- Art is off with `--json`, `NO_COLOR`, `CI`, a dumb `TERM` or the playful setting turned off.

## Marks come from one drawing

- The swarm, `mark.svg`, is the site logo, favicon and theme logo.
- The bust, `favicon.svg` and the app icons, is for the web app and the notifier.
- All of them derive from `web/src/components/heinzel.ts`. Regenerate them with `make icons`.

## Domains

- `heinzel.team` is the canonical domain.
- `hz.team`, `heinzelmaennchen.team` and `heinzelmännchen.team` redirect to it.
