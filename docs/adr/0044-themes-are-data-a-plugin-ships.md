---
status: proposed
date: 2026-10-24
decision-makers: maintainer
related: 0003, 0012, 0017, 0026, 0027, 0028, 0037, 0039, 0040
---

# 0044: Themes are data a plugin ships: `module.theme(...)` offers them, `hz system theme use` picks one

## Context and Problem Statement

The web head ([ADR 0039](0039-a-web-head-a-react-app-served-by-the-api-head.md)) has one look: its colours, fonts and
radii are CSS custom properties in `web/src/index.css`, light and dark. The maintainer asked for a theming system: a
plugin can offer one or more themes (colours for light and dark, fonts, border radius and width), and the person
switches between the ones installed. A company's layer plugin ([ADR 0040](0040-layers-and-playbooks-are-plugins.md))
is the obvious place for its look, and the public reference company `heinzel-acme` should prove it. Every value of a
theme ends up inside a CSS declaration in the person's browser, under a CSP that allows no inline script and no
remote font (`font-src 'self'`). So the questions are what a theme is, how a plugin declares one, which one wins when
two plugins offer the same name, where the choice is kept, and what keeps a theme's values from becoming CSS of their
own.

## Decision Drivers

- Heads are views ([ADR 0003](0003-heads-are-views-on-operations.md)): the web app gets the themes and the choice
  from operations, the CLI and MCP get the same ones, and a plugin needs no UI code to offer a theme.
- A theme is data, not code: it can be listed, checked and tested without a browser, and it can't run anything.
- A value that becomes a CSS custom property must not be able to end its declaration (`;`, `}`), load anything
  (`url(`), or reach another property (`var(`): checked when the plugin is imported, not trusted in the browser.
- Nothing is sent home ([ADR 0027](0027-nothing-is-sent-home.md)): no web font service; fonts are the machine's own.
- Declared beats discovered by convention, and one mechanism for what a layer brings (ADR 0037, ADR 0040).

## Considered Options

- **A `ThemeLet` per theme, declared with `module.theme(Theme | Path)`; a theme file is TOML; consumed by
  `system.theme`; `[system.theme] name` is a user setting** (chosen)
- **A `themes/` directory found by convention in every plugin package**, like a playbook folder: ADR 0037 rejected
  exactly this for playbooks (another loader, nothing the kernel's let listing sees, no check at import), and a
  plugin's module already is the place it declares what it brings. The chosen option keeps the files (a designer
  edits TOML, not Python) and makes the module name them.
- **Themes as recommended settings of a playbook** (`defaults={"system.theme...": ...}`): a playbook may only
  recommend capability bindings and its own module's settings (ADR 0037), and a theme is a set of thirty-five values,
  not one setting; a person would have to accept each.
- **Free-form CSS from the plugin** (a stylesheet or a map of any custom property): the most flexible and the least
  safe; every new property would be a way to restyle or hide anything, and the app's tokens would stop being a
  contract a theme fills.
- **Remote fonts or bundled font files now**: remote fonts break ADR 0027 and the CSP. Bundled files need a route
  that serves a plugin's assets under `'self'` and a format check; left as an extension (More Information).
- **Theme selection in the browser only** (`localStorage`): the CLI and MCP couldn't see or set it, a second browser
  starts from the default, and a layer can't one day recommend one.

## Decision Outcome

Chosen option: **a `ThemeLet` per theme, declared with `module.theme(...)` and consumed by `system.theme`**, because a
theme stays typed data the kernel collects like a playbook, every value passes a grammar that can't leave its CSS
declaration, and the choice is an ordinary setting every head reads.

- **A theme.** `Theme` (pydantic, `heinzel`): `name` (a slug, at most 64 characters), `title` (one printable line, at
  most 64), `light` and `dark` (`ThemeColours`), `font_sans`, `font_mono`, `radius` and `border_width`; unknown keys
  are refused. `ThemeColours` has exactly the app's tokens: `background`, `foreground`, `muted`, `muted_foreground`,
  `card`, `border`, `primary`, `primary_foreground`, `accent`, `ok`, `warn`, `bad`, `ring`, `sidebar`, `input`,
  `popover`; each becomes `--color-<token>` with underscores as dashes. Fonts, radius and border width are the same in
  both modes (`--font-sans`, `--font-mono`, `--radius`, `--border-width`).
- **The grammar.** Colours are `#rgb` or `#rrggbb`, kept lowercase. Lengths are `0` or a number of at most three
  integer and four decimal digits with `px`, `rem` or `em`: no `calc()`, `var()`, percentages or viewport units. A
  font stack is at most 300 characters of family names separated by commas; a name is unquoted words of letters,
  digits and dashes (`system-ui`, `-apple-system`, `Avenir Next`) or a double-quoted name of letters, digits, spaces,
  dots, underscores and dashes (`"SF Mono"`). Nothing else passes: no `;`, `{`, `}`, `(`, backslash, single quote,
  quote inside a name, comment or newline. The fonts are those installed on the machine; a stack ends in a generic
  family so a missing font falls back.
- **Declaration.** `module.theme(source)` takes a `Theme`, a `*.toml` file of one, or a directory of them (read in
  name order), read and checked at import like a playbook's templates; each theme is one `ThemeLet` named after it, so
  a module can offer several and never two of one name. The name `default` is refused: it is the built-in theme
  (`DEFAULT_THEME` in `heinzel.kernel`, the app's palette of `web/src/index.css` as hex), which always exists.
- **Ties.** When several modules offer a theme of the same name, the more specific layer of the person's stack wins
  (ADR 0040: company, org and site, team), then any layer in the stack over a module outside it, then the first
  module by qualified name. The list names the hidden ones (`shadows`), so nothing is overridden silently.
- **Operations** (`system.theme`, [ADR 0018](0018-one-administrative-namespace.md): about Heinzelmännchen itself):
  `hz system theme list` (read-only; every head, the web head included) returns
  `{selected, themes: [{name, title, module, plugin, selected, light, dark, font_sans, font_mono, radius,
  border_width, shadows}], note, next}`, the built-in theme first. `hz system theme use <name>` refuses a name no
  installed plugin offers and writes `[system.theme] name` to the user file through `ConfigEditor`. Both read the
  configuration at every call, so a running `hz ui` sees a pick made in a terminal.
- **The setting.** `[system.theme] name = "default"` (`HZ_SYSTEM_THEME__NAME`), user scope: a project `hz.toml` can't
  set it (`ignored_in_project`), because a repository someone else controls must not decide how a person's tool
  looks. A configured theme whose plugin is gone falls back to `default`, and the list says why and what to run.
- **The web app** applies the selected theme's values as custom properties on `<html>` (its own work, outside this
  record): it sets only the properties above, from the operation's typed result, never from free text.
- **Public API**, additive ([ADR 0026](0026-plugin-api-versioned-with-semver.md)): `Theme`, `ThemeColours`,
  `ThemeLet` and `Module.theme` in `heinzel`; `DEFAULT_THEME` and `font_stack_problem` in `heinzel.kernel`.

### Consequences

- Good: a company's look is a TOML file and one line in its layer plugin; teams diverge by offering the same name in
  a more specific layer, as with everything else a layer brings.
- Good: the browser only ever receives values a strict grammar accepted at import, so a theme can't inject CSS, load
  a remote resource or hide part of the app.
- Good: the CLI and agents see and switch themes like any other setting.
- Bad: fonts are limited to what is installed; a company font needs the bundled-files extension below.
- Bad: the token set is now a contract between plugins and the app: a new token in the app is a new required field,
  and so a breaking change for every theme unless it gets a default derived from another token.
- Neutral: a layer can't yet recommend its theme; the person picks it. A theme recommendation would extend ADR 0037's
  list of what a playbook may recommend.

### Confirmation

- `tests/unit/kernel/themes_test.py`: the grammar of colours, lengths, font stacks (injection attempts: `;`, `}`,
  `url(`, `var(`, quotes, backslashes, comments, newlines) and titles; unknown and missing tokens; reading theme files
  and directories with errors that name the file and field; `module.theme` refusing `default` and a second theme of one
  name.
- `tests/integration/theme_test.py`: with the Acme test layers (`tests/fixtures/layers`), the list through the CLI and
  through the web head behind its token, the field names above, the more specific layer winning a name over the
  company and over a plugin outside the stack, `use` writing the user file and refusing an unknown name, a running web
  head seeing a pick made elsewhere, the fallback when a plugin is gone, the environment and a project file.
- `tests/unit/public_api_test.py`: the new names.

## More Information

- Requested by the maintainer, 2026-10-24: themes in plugins with light and dark colours, fonts, border radius and
  width; switching between those available; the reference company's theme as the proof.
- The reference company `heinzel-acme` ships `themes/acme.toml` from its company layer plugin.
- Extension, not decided: bundled font files (`fonts=` on a theme, served by the web head from the plugin under
  `'self'`, `woff2` only, with `@font-face` built from checked data); a theme a layer recommends; a per-mode font
  stack if a theme needs one.
