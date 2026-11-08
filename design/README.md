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

- **Integrated title bar (2026-11-07).** The macOS desktop app draws no native title bar: the window's top 44 px is
  one row in the sidebar colour with a hairline below, made of the sidebar's first line (traffic lights in the first
  80 px, then the navigation toggle, Home, Bell and Search), the main column's top bar and the side panel's tabs (or
  the chat header when there are no tabs). The row drags the window; every control in it does not. The theme's
  lockup moves to the line below it and carries the name the window chrome no longer shows. Collapsed, the expand
  button stays after the lights and the main header starts after it; full screen gives the lights' inset back. A
  dialog, menu or popover over the row is no-drag, so its top edge stays clickable. A
  browser or PWA has no bridge, so its layout is unchanged; Windows and Linux keep the native frame for now.
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
- **Choices (2026-11-07).** A choice among two to four exclusive options is a segmented control
  (`components/ui/segmented.tsx`): a `radiogroup` of `radio` segments on a `color-muted` track; the chosen segment is
  filled `color-primary` with `color-primary-foreground`, never the `color-accent` hover fill alone, and the fill is the
  one accent in that container. A longer exclusive set (filter chips) is still radios. `aria-pressed` is for one
  independent on/off toggle (the top bar's panel buttons), never for a member of a set; `choices-standards.test.ts`
  fails an `aria-pressed` set. A choice the app cannot offer stays in the group, dimmed and `aria-disabled`, with its
  reason in the tooltip and the status line. Choosing while another option is live switches in one step: the new one
  is asked for first, the old one stopped only once the new one runs. Segmented controls and switches are content, so
  a pane made of them has no action row. Example: the Audio pane is Show (Spectrum | Aurora | Both) and Listen to
  (Off | Microphone | Shared audio) segmented controls, a live status line, one privacy line and the master switch.
- **Audio visualizations follow the theme (2026-11-07).** The Spectrum rail and the Aurora take every hue from the live
  theme tokens (`color-primary`, `color-ok`, `color-warn`) in any CSS colour notation, re-read the moment the mode,
  theme or its daypart variant changes; no colour is written in `web/src/audio` (`choices-standards.test.ts`). The
  Aurora is a full-window layer under the content, so the page colour is painted on `<html>` and `<body>` stays
  transparent; a painted `<body>` hides it.

## Content in focus, no screen space wasted

The content the person came for gets the space; everything else earns its place. This is a first-class principle,
checked in review like the brand rules:

1. **Never repeat a title.** When a header selector already names the page, the page has no second heading saying
   the same; keep one.
2. **One action is one icon button** in the header zone; several are one bottom row under a divider
   ([Actions](#actions)).
3. **No empty bands or decorative gaps.** Spacing comes from one compact scale (Tailwind steps 1, 1.5, 2, 3, 4);
   a gap larger than that needs a reason.
4. **Chrome earns its space.** Sidebars, footers, side panes and toasts are collapsible and hidden when empty. Rarely
   needed facts live in hover, focus or tap panes, never in persistent text.
5. **Content takes all remaining space** on desktop, tablet and phone: no fixed widths that leave the rest empty, no
   chrome that stays when it has nothing to say.
6. **Scrollbars overlay and hide** when not scrolling.
7. **Toasts and banners are dismissible and never stack**; a newer one replaces the older one of the same kind.
8. **A date is said once:** group headers carry dates, rows carry times.

The audit in [research/design-action-audit.md](../research/design-action-audit.md) measures, per screen, how much of
the viewport is content at 1440x900 and 390x844.

## Actions

An action is a button that does something or goes somewhere. Every container (a footer or sidebar pane, a popover, a
card, a dialog, a panel, a page, a settings section, a toast) places its actions the same way. Rules, then examples.

1. **Two places only.** Chrome (what acts on the container itself: Close, Dismiss, Collapse, Expand, New) sits at the
   right of the title row as ghost icons, at most three. Content actions (what you do with what the container says) sit
   in one bottom row under a divider, after all content. Nothing after the row; no action inside the content or the
   title text. Switches and checkboxes are content and stay beside the text they change.
2. **The first action leads.** It shows icon and words. Every other action is icon-only; its words are the tooltip and
   the accessible name. Authors order actions: lead first, then navigation, then commands, destructive last.
3. **One row, never wrapped**, at every width on desktop and phone. When the lead's words would overflow the row, the
   row draws the lead as an icon too (measured once before paint); nothing else moves. Words never truncate.
4. **Dialogs answer a question**, so their row is right-aligned and every action keeps its words: Cancel (outline)
   first, the committing action (filled) last, red when it destroys something. Inline confirmations follow the same
   order. A destructive action anywhere else is a red icon that opens such a dialog.
5. **One look.** Buttons are 32px high (44px on touch), icon-only buttons as wide as high, 16px icons, 6px apart.
   Content actions are outlined; the one `primary` per container is filled; chrome and list-row actions are ghost.
   A disabled action is the same button dimmed. Pages put at most two outlined icon+words actions in their header.
6. **No links as actions.** A text link is only a reference inside prose (`data-prose-link` outside `Prose`). `to=`
   navigates inside the app (a real link styled as a button), `href=` opens outside it in a new tab.
7. **Exception:** the version pane (about card and release notes are prose), marked `freeform` with its reason.

```text
┌──────────────────────────────────────────┐
│ Title                            [v] [x] │  title row: words, then chrome: ghost icons, at most three
│ Body: status, facts, lists, switches,    │  content: what the container says
│ messages, errors ("Copied", "Failed")    │
├──────────────────────────────────────────┤  divider, drawn by PaneActions, never by hand
│ [+ Create a personal Heinzel] [💬]       │  one row: the first action with words, the rest icons
└──────────────────────────────────────────┘
```

### Examples

| Container | Good |
|---|---|
| Heinzelmännchen pane, empty | "No persisted Heinzels yet."; divider; [+ Create a personal Heinzel] (primary) [💬] |
| Calendar pane | Now and next meeting; divider; [📅 Open calendar] |
| Calendar side pane | Day-grouped agenda, dates in sticky day headers, times in rows; divider; [📅 Today] [‹] [›] [🔧] |
| System health pane | Check summary; divider; [🩺 Open Doctor] [⟳] |
| Updates pane | Status lines, automatic-updates switch; divider; [⬇ Update now] [⟳] |
| Daemon pane (plugin) | Status lines; divider; [⚡ Restart daemon] [📄] [⏱] [⚠] (red) |
| Audio visualizer pane | Segmented Show and Listen to, status, master switch; no action row (all content) |
| Today card | title row with [x] Dismiss; body; divider; [↗ Open] [💬] |
| Operation dialog | title row with [x]; question; divider; right: [Cancel] [Install] |
| Plugins list row | ghost [⏻] [🗑] (red); Remove opens a confirming dialog |
| Version pane (exception) | About card with the release link, release notes as prose; no action row |

Bad: an underlined "Open chat" line; two rows of buttons; a refresh button between two status lines; "Ask about it"
top-right on one card and bottom-left on the next; a wizard that puts Next before Back; a Remove that just removes.

### How it is enforced

- `web/src/components/PaneActions.tsx` holds the one action row (`PaneActions`, `align`, `words`), the one action
  (`PaneAction`, `primary`, `destructive`, `ghost`, `to`, `href`) and the title row with chrome (`PaneTitle chrome`).
  `FooterItem actions={…}` draws the row; plugins get the same components from the SDK and the same renderer for data.
- In development and tests, every open container is checked: a button or link outside the row, content after it, a
  second row, an action that is not a `PaneAction`, one without an icon or a name, words on more than the lead, and
  actions on more than one line fail loudly.
- `web/src/lib/pane-actions-standards.test.ts` fails on a text link used as an action, a hand-drawn `border-t`
  row, a right-aligned button row outside `PaneActions`, an icon button without a name, a raw `<button>` outside the
  listed kinds (switch, tab, menu row, nav) and an unlisted `freeform` container.
- The mobile browser sweep opens every footer pane, one dialog and one Today card at phone width on Chromium and
  WebKit and asserts one row, a lead with words or a declared fallback, and 44px targets.
