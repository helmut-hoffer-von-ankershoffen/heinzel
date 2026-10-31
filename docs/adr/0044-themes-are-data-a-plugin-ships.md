---
status: proposed
date: 2026-10-24
decision-makers: maintainer
related: 0003, 0012, 0017, 0026, 0027, 0028, 0037, 0039, 0040
amended-by: 0060, 0064, 0111
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
  font stack is one line of at most 300 printable ASCII characters: family names separated by commas, each either
  unquoted words that start with a letter (one leading dash allowed) and hold letters, digits and dashes
  (`system-ui`, `-apple-system`, `Avenir Next`) or a double-quoted name of letters, digits, spaces, dots, underscores
  and dashes (`"SF Mono"`); spaces around a name are dropped. Nothing else passes: no `;`, `{`, `}`, `(`, backslash,
  single quote, quote inside a name, comment, newline or other control character, and no unquoted CSS-wide keyword
  (`inherit`, `initial`, `unset`, `revert`, `revert-layer`, `default`). The fonts are those installed on the machine;
  ending a stack in a generic family (`sans-serif`, `monospace`) is advice, so a missing font falls back.
- **Declaration.** `module.theme(source)` takes a `Theme`, a `*.toml` file of one, or a directory of them (read in
  name order), read and checked at import like a playbook's templates; each theme is one `ThemeLet` named after it, so
  a module can offer several and never two of one name. A `ThemeLet` checks its theme again when it is created, so a
  theme built without validation (`Theme.model_construct`) or a let added by hand can't skip the grammar, and both
  models revalidate nested instances. The name `default` is refused: it is the built-in theme (`DEFAULT_THEME` in
  `heinzel.kernel`, the app's palette of `web/src/index.css` as hex), which always exists.
- **Ties.** When several modules offer a theme of the same name, the more specific layer of the person's stack wins
  (ADR 0040: company, org and site, team), then any layer in the stack over a module outside it, then the first
  module by qualified name. The list names the hidden ones (`shadows`), so nothing is overridden silently. ADR 0040
  leaves open which of an org and the site wins; until it is decided, a name an org and the site both offer, with
  neither the team nor a more specific layer offering it, is left out of the list and reported in `conflicts` with
  who can decide it (the team, by offering the name), the same "team decides, no silent merge" rule ADR 0040 applies
  to settings.
- **Operations** (`system.theme`, [ADR 0018](0018-one-administrative-namespace.md): about Heinzelmännchen itself):
  `hz system theme list` (read-only; every head, the web head included) returns
  `{selected, themes: [{name, title, module, plugin, selected, light, dark, font_sans, font_mono, radius,
  border_width, shadows}], conflicts, note, next}`, the built-in theme first. `hz system theme use <name>` refuses a name no
  installed plugin offers and writes `[system.theme] name` to the user file through `ConfigEditor`. Both read the
  configuration at every call, so a running `hz ui` sees a pick made in a terminal.
- **The setting.** `[system.theme] name = "default"` (`HZ_SYSTEM_THEME__NAME`), user scope: a project `hz.toml` can't
  set it (`ignored_in_project`), because a repository someone else controls must not decide how a person's tool
  looks. A configured theme whose plugin is gone, or a value that isn't a theme name at all, falls back to `default`,
  and the list says why and what to run; it never fails, so the web app always has a theme to apply.
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
  `url(`, `var(`, quotes, backslashes, comments, newlines, non-ASCII characters, CSS-wide keywords) and titles;
  unknown and missing tokens; themes built with `model_construct` checked again; reading theme files and directories
  with errors that name the file, the field and the rule; `module.theme` refusing `default` and a second theme of one
  name.
- `tests/integration/theme_test.py`: with the Acme test layers (`tests/fixtures/layers`), the list through the CLI and
  through the web head behind its token, the field names above, the more specific layer winning a name over the
  company and over a plugin outside the stack, an org and the site offering one name as a reported conflict, `use`
  writing the user file and refusing an unknown name, a running web head seeing a pick made elsewhere, the fallback
  when a plugin is gone or the configured value is no theme name, the environment and a project file.
- `tests/unit/public_api_test.py`: the new names.

## More Information

- Requested by the maintainer, 2026-10-24: themes in plugins with light and dark colours, fonts, border radius and
  width; switching between those available; the reference company's theme as the proof.
- The reference company `heinzel-acme` ships `themes/acme.toml` from its company layer plugin.
- Open with ADR 0040: the org-site precedence; when it is decided, themes follow the same rule as settings.
- Extension, not decided: a theme a layer recommends; a per-mode font stack if a theme needs one. (Bundled font
  files, listed here first, are decided in the amendment below.)

**Amendment 2026-10-24: type scale, density, icon colours and bundled fonts.** Requested by the maintainer the same day: a theme sets the app's type scale, density and icon look, and ships its own
font files. Additive: every new field has a default, so a theme written before this stays valid (the token-contract
consequence above, met by defaults rather than new required fields).

- **Two colours** in `ThemeColours`, both modes: `accent_foreground` (text and icons of the active navigation item
  and of links; `foreground` when not given) and `icon` (icons that aren't active; `muted_foreground` when not given),
  hex like the others, as `--color-accent-foreground` and `--color-icon`.
- **Type and density** on `Theme`, with the built-in values the app uses: `font_size` (`15px`), `font_size_small`
  (`13px`), `icon_size` (`18px`), `row_height` (navigation and list rows, `36px`), `radius_control` (buttons, inputs,
  navigation items, `0.5rem`), `radius` (cards and dialogs, now `0.75rem` by default and in the built-in theme),
  `heading_weight` (`600`), `heading_tracking` (`0`) and `spacing` (Tailwind's base step, `0.25rem`). Lengths take the
  length grammar above; `heading_tracking` may also be negative (`-0.5px`); `heading_weight` is an integer from 100 to
  900 (a TOML integer, not a string). The list returns each under its own name.
- **The built-in theme** takes the app's current palette (`web/src/index.css`): neutral greys with a terracotta
  primary, light and dark.
- **Font files.** A theme file may declare `[[fonts]]`, at most 16: `family` (what its stacks call it: letters,
  digits, single spaces, dots, underscores and dashes, at most 64), `file` (a path relative to the theme file's
  directory: at most eight plain names of letters, digits, dots, underscores and dashes, none starting with a dot,
  ending in `.woff2`), `weight` (`"400"` or a variable font's `"100 900"`, 100 to 900, low to high) and `style`
  (`normal` or `italic`). At import the file must exist, resolve (symlinks followed) inside the theme's directory, be
  a file, at most 2 MB, and start with the woff2 signature; a `Theme` built in Python can't ship fonts (no directory
  to be relative to). The let keeps the directory (`ThemeLet.directory`).
- **Serving.** The web head serves font `n` of a theme at `/theme-fonts/<theme>/<n>.woff2`, outside `/api` and without
  the token, because the browser fetches fonts for `FontFace` itself and can't send the header; nothing in a font
  file is private. Every other check of the web head applies (a loopback Host on its port, no other Origin, no
  cross-site or same-site `Sec-Fetch-Site`), and the CSP's `font-src 'self'` already allows it. The request matches a
  theme-name pattern and a number, never builds a path, and asks `ThemeFonts` (an interface in the kernel,
  implemented by `system.theme`, so the head knows no module) for the theme that wins its name; the file is checked
  again as it is read, through one descriptor that follows no symlink, waits on no FIFO and must be a regular file,
  and on the bytes read, so a file swapped or removed since import is refused (`404`, a fixed message). Answers are
  `font/woff2`, `nosniff`, `Cache-Control: public, max-age=31536000, immutable`.
- **The list** returns `fonts: [{family, url, weight, style}]` per theme; `url` is the route above with `?v=`, a
  hash of the file's content, so a changed font is a new URL and the long cache is
  safe. A font whose file is gone is left out. The web app loads each with the `FontFace` API, so no `@font-face`
  text, inline style or new CSP source is needed.
- **Public API**, additive: `ThemeFont` and `ThemeFonts` in `heinzel`, `FONT_ROUTE` in `heinzel.kernel`.
- **Confirmation**: `tests/unit/kernel/themes_test.py` (each new field's grammar with injection attempts, defaults for
  older themes, font paths that leave the directory by `..`, absolute paths, backslashes and symlinks, the size and
  signature checks, a file swapped after import) and `tests/integration/theme_test.py` (the list's shape, a font served
  with its type and cache headers and without the token, refused for an unknown index, theme or file name, for the
  theme a more specific layer hides, and for another Host, Origin or site).

**Amendment 2026-10-24: logos and a main tint.** Requested by the maintainer the same day: a theme brings its logo,
and the main content area can take a tint of its own. Additive like the amendment above: both are optional, so every
theme written before stays valid.

- **A logo.** A theme file may declare `[logo]` (`ThemeLogo`): `light` (the logo on a light background), `dark`
  (`light` when not given), `mark` (a square one for narrow places such as a collapsed sidebar; none when not given),
  `alt` (what it says, for screen readers: one printable line, at most 64 characters, required) and `height` (the
  length grammar above, `24px` by default). The files follow the font files' path rules (relative, plain names, none
  starting with a dot, inside the theme's directory after symlinks are resolved, a regular file), end in `.svg` and
  are at most 200 KB. At import each must be UTF-8 XML whose root is `<svg>`, and is refused if it holds a DOCTYPE or
  an entity (so nothing expands or loads before parsing), a `<script>` or `<foreignObject>` (case-insensitive), an
  event-handler attribute (`on...=`), an `href`, `xlink:href` or `src` that isn't a same-document fragment (`#id`),
  a `url(...)` other than `url(#id)`, `@import`, `image-set(`, an `xml-stylesheet` link, or a `<set>`/`<animate>` that
  rewrites a reference or a handler: no external file, no `data:` URI. As with fonts, a `Theme` built in Python can't
  ship one.
- **Serving.** The web head serves them at `/theme-assets/<theme>/<light|dark|mark>.svg` (`LOGO_ROUTE`), outside
  `/api` and without the token, because an `<img>` can't send the header, under every other check the font route has,
  through `ThemeFonts.logo` (the same interface, so the head still knows no module), re-checked on read through the
  same descriptor that follows no symlink and on the bytes read. Answers are `image/svg+xml`, `nosniff`, the font
  route's immutable cache, and a CSP of their own, `default-src 'none'; style-src 'unsafe-inline'` (`LOGO_CSP`), so a
  logo opened on its own loads and runs nothing even if a check missed something.
- **The list** returns `logo: {light, dark, mark, alt, height} | null` per theme: relative URLs with `?v=` (a hash of
  the content), `dark` the light one's URL when the theme declares none or its file is gone, `mark` null when there is
  none; a logo whose light file is gone is null.
- **The built-in theme's logo** is a swarm of Heinzelmännchen (`heinzel-swarm.svg`, which `make icons` copies) and its
  name as a wordmark, light and dark with explicit fills, and the mark alone: package data of the kernel
  (`heinzel/kernel/assets/theme`), as `DEFAULT_LOGO` beside `DEFAULT_THEME`, served for the theme name `default`.
  `DEFAULT_THEME` itself names no logo, so a plugin can still copy it into a theme of its own.
- **A main tint.** `ThemeColours.main_tint` (both modes, optional, so light and dark can differ): a hex colour behind
  the main content area, `--color-main-tint`, which the web app sets only when a theme gives one.
- **Public API**, additive: `ThemeLogo` in `heinzel`; `ThemeLogo`, `ThemeLogoFiles`, `DEFAULT_LOGO`, `LOGO_ROUTE`
  and `LOGO_CSP` in `heinzel.kernel`; `ThemeFonts.logo`.
- **Confirmation**: `tests/unit/kernel/themes_test.py` (logo paths that leave the directory by `..`, absolute paths
  and symlinks, the size limit, files that aren't SVG, scripts, handlers, `foreignObject`, external and `data:`
  references, DOCTYPEs and entities, same-document references allowed, the defaults and the dark fallback, a file
  swapped after import, the built-in logo's files, `main_tint`'s grammar) and `tests/integration/theme_test.py` (the
  list's shape with the built-in and Acme logos, a logo served with exactly the headers above and without the token,
  refused for an unknown name or theme and for the theme a more specific layer hides, and for another Host, Origin or
  site).
