# Design

The design system's source of truth. [tokens.json](tokens.json) holds the values; the app, the product site and a
drift test read them. This page holds the rules that are not numbers. Names, taglines and the marks are in
[docs/BRANDING.md](../docs/BRANDING.md); themes as data in [docs/THEMES.md](../docs/THEMES.md).

## Core idea

Heinzelmännchen is a team of Heinzels working together on behalf of people (owner, 2026-11-07). Everything else
follows from it:

- **The product mark is plural.** A team is the logo: the swarm wherever the product presents itself (the sidebar
  header and rail, the theme logo, the site). A single Heinzel is one voice: the chat, a testimonial, a named Heinzel
  on its own page. The app icon (favicon, notifier) stays the single bust, as docs/BRANDING.md has it: an icon names
  the app, a logo shows the team.
- **Copy is plural by default.** "Your Heinzels" draft, prepare and hand over; one Heinzel speaks only where one is
  meant ("Ada drafted this"). Empty states, onboarding and imagery show Heinzels as colleagues who act for the
  person and ask before anything changes.
- **Today shows what each Heinzel is doing for you,** by name and job, not a single assistant's feed.
- **Hand-offs between Heinzels are visible:** when one Heinzel's draft feeds another's, the chain is shown, so the
  person sees the team at work rather than a black box.
- **Approvals are addressed to the human.** A Heinzel asks the person; Heinzels never approve each other's work on
  the person's behalf.

## Decisions

- **Sidebar header (2026-11-07).** The header is the product lockup, not another navigation row: the swarm mark (the
  drawing `mark.svg` is made from, drawn inline) at 28 px, and the name as live text one pixel above the UI size
  (16 px by default), semibold, tracking -0.01em, centred on the mark with an 8 px gap; it fits whole in the 256 px
  column with the system fonts and truncates in a narrower one. The lockup links to Home, named "Heinzelmännchen
  home" so it is told apart from the Heinzels screen's link, which carries the plain name. The row is 48 px like the
  top bar, so both hairlines run as one across the window; the collapse button is the top bar's 36 px icon button
  with a 44 px hit area, and the collapsed rail shows the same mark at 28 px: its figures are 19 px tall there, hats
  and shirts still read, so no simplified crop is needed down to 28 px (a three-figure crop is the next step if a
  smaller slot appears). Before, one Heinzel's bust at 25 px stood beside the name at the navigation's own size and
  weight, so the header read as a row and the picture looked too big for the words. A theme that ships a wordmark
  of its own still shows it as an image at its declared height.
- **Heinzels screen icon (2026-11-07).** The navigation item for the Heinzels screen is the team glyph (one face
  with two caps behind it, `HeinzelTeam`), in the line style of its neighbours; the single face (`HeinzelFace`)
  stays for one Heinzel: a graph node, a plugin's contribution.

## Content in focus, no screen space wasted

The content the person came for gets the space; everything else earns its place. This is a first-class principle,
checked in review like the brand rules:

1. **Never repeat a title.** When a header selector already names the page, the page has no second heading saying
   the same; keep one.
2. **One action is one icon button** in the header zone; several are one bottom row under a divider
   ([Pane actions](#pane-actions)).
3. **No empty bands or decorative gaps.** Spacing comes from one compact scale (Tailwind steps 1, 1.5, 2, 3, 4);
   a gap larger than that needs a reason.
4. **Chrome earns its space.** Sidebars, footers, side panes and toasts are collapsible and hidden when empty. Rarely
   needed facts live in hover, focus or tap panes, never in persistent text.
5. **Content takes all remaining space** on desktop, tablet and phone: no fixed widths that leave the rest empty, no
   chrome that stays when it has nothing to say.
6. **Scrollbars overlay and hide** when not scrolling.
7. **Toasts and banners are dismissible and never stack**; a newer one replaces the older one of the same kind.

The audit in [research/design-action-audit.md](../research/design-action-audit.md) measures, per screen, how much of
the viewport is content at 1440x900 and 390x844.

## Pane actions

A pane is any bounded surface with a title: a footer pane, a sidebar pane, a popover, a settings section, a panel
or a dialog. Rules (short on purpose; examples below):

1. **Structure:** content, then one divider, then one row of action buttons. Nothing after the row, no action in
   the content or the title.
2. **Icon-only by default**, named by its words: the words are the accessible name and the tooltip ("Open chat").
3. **Words beside the icon only with a reason** (`withText="…"`): the icon alone would be ambiguous, or the action
   is consequential (installs, restarts, captures, deletes).
4. **One row, never wrapped**, at every pane width on desktop and phone. When it would wrap, make actions icon-only.
5. **No links as actions.** A text link is only a reference inside prose.
6. **Exception:** the version pane (about card and release notes are prose), marked `freeform` with its reason.

```text
┌──────────────────────────────────────────┐
│ Title                                    │  title row: words only
│ Body: status, facts, lists, switches,    │  content: what the pane says; switches and checkboxes stay
│ messages, errors ("Copied", "Failed")    │  here, beside the text they change
├──────────────────────────────────────────┤  divider, drawn by PaneActions, never by hand
│ [+] [💬] [⟳ Restart and apply]           │  one row: icon-only buttons, words only with a reason
└──────────────────────────────────────────┘
```

### Which control

| The action | Control |
|---|---|
| Simple or obvious: open chat, open calendar, create, check again, refresh, retry, share, copy, cancel | `PaneAction` (icon-only, tooltip and accessible name from its words) |
| The action the pane exists for (empty state "Create a personal Heinzel") | `PaneAction primary`, icon-only unless consequential |
| Consequential: install an update, restart and apply, start a capture | `PaneAction withText="why"`, icon and words |
| Destructive: Uninstall, Remove, Delete | `PaneAction destructive withText="why"`, last, confirms in a dialog |
| A plugin action without its own icon | Words beside the generic effect icon (`withText`), set by the renderer |
| A reference inside a sentence, help text, docs, legal pages | Inline text link, marked `data-prose-link` outside `Prose` |
| A place outside the app | `href=`: opens in a new tab |

Also: one primary at most; order primary, navigation, commands, destructive last; at least 32px high, 44px on
touch; a disabled action is the same button dimmed; `to=` for navigation inside the app (a real link styled as a
button), never `onClick={() => navigate(…)}`.

### Examples

| Pane | Good |
|---|---|
| Heinzelmännchen, empty | "No persisted Heinzels yet."; divider; [+] Create a personal Heinzel (primary), [💬] Open chat |
| Calendar | Now and next meeting; divider; [📅] Open calendar |
| System health | Check summary; divider; [🩺] Open Doctor, [⟳] Refresh health checks |
| Updates | Status lines, last installed, automatic-updates switch; divider; [⟳] Check again, [⬇ Manually update…] |
| Plugins changed | Changes and the question; divider; [⟳ Restart and apply Plugins] (primary, words), [✕] Cancel |
| Audio visualizer | Status, Spectrum/Aurora checkboxes, master switch; divider; [🖥 Share audio] [🎤 Use microphone] |
| Tailscale (plugin) | Status, switch and URL; divider; [↗] Share, [⧉] Copy, [↗] Open |
| Version (exception) | About card with the GitHub release link, release notes as prose; no action row |

Bad: an underlined "Create a personal Heinzel" line above the divider and "Open chat" below it; two rows of buttons;
a refresh button between two status lines.

### How it is enforced

- `web/src/components/PaneActions.tsx` holds `PaneActions` and `PaneAction`; `FooterItem actions={…}` draws the row.
  Plugin panes declared as data get the same renderer.
- In development and tests, `FooterItem` checks every open pane: a button or link outside the row, content after
  it, a second row, an action that is not a `PaneAction`, one without an icon or a name, and actions on more than
  one line fail loudly.
- `web/src/lib/pane-actions-standards.test.ts` fails on a text link used as an action, a title-row action, an
  unlisted `freeform` pane and an unlisted `withText` action (each needs its reason).
- What a test cannot judge (whether an icon is clear, real wrapping at phone widths) is checked in review and in the
  mobile browser sweep's screenshots; the audit is in [research/design-action-audit.md](../research/design-action-audit.md).
