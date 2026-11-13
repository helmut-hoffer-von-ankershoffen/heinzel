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

- **Pane microcopy (2026-11-08).** A control in a pane, card, popover or settings row has at most one status
  line, and the status line says state first ("Turning on...", "Off", the URL), then the one thing to do
  ("Sign in to Tailscale, then try again."). No sentence in a pane is longer than about 60 characters
  (`contrib.toggle.*`, `daemon.*`, `lifecycle.*`, `notify.*`, `updates.*`, `pluginsPending.*`, provider `status`
  and `DetailLine.text`); longer content belongs in Details (a disclosure), a tooltip, the CLI `--json` `message`
  or docs. No internal state words reach the person: not *requested, applied, verified, owning, apply, intent,
  mapping, origin, handler, instance, readiness*; say what they see ("on", "not finished", "the link", "the port").
  A switch shows the person's choice; readiness is the status line, never the switch's word. Text under a control
  explains the state, never names a second place to go ("from the owning web app"); if the fix is elsewhere, the
  button takes them there. Every pane has exactly one primary action, an icon button with tooltip and aria-label
  for a simple action (Copy, Open) or icon+text for a verb that changes something (Restart hz); never a text link.
  Copy from a backend `message` is never shown raw: the contract carries a short `status`/`problem` (<= 60) and the
  long `explanation` separately, and the kernel validates the short fields. `web/src/test/copy-standards.test.ts`
  fails a new violation;
  an allowlist entry needs a reason in the test file.
- **Integrated title bar (2026-11-07).** The macOS desktop app draws no native title bar: the window's top 44 px is
  one row in the sidebar colour with a hairline below, made of the sidebar's first line (traffic lights in the first
  80 px, then the navigation toggle, Home, Bell and Search), the main column's top bar and the side panel's tabs (or
  the chat header when there are no tabs). The row drags the window; every control in it does not. The theme's
  lockup moves to the line below it and carries the name the window chrome no longer shows. Collapsed, the expand
  button stays after the lights and the main header starts after it; full screen gives the lights' inset back. A
  dialog, menu or popover over the row is no-drag, so its top edge stays clickable. A pane over the window
  (Notifications) starts under the row (`--titlebar-height`, 0 in a browser), so its header never meets the lights
  or the drag region; a standards test keeps every drag selector's controls no-drag. A
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
- **Choices (2026-11-07).** Exclusive sets of two to four are a segmented control (`components/ui/segmented.tsx`): a
  `radiogroup` of `radio` segments on one `color-muted` track, one `color-primary` fill with
  `color-primary-foreground`, never the `color-accent` hover fill alone. A longer exclusive set (filter chips) is
  still radios. Independent sets are checkboxes: compact, a chip group (a native checkbox in a label, 2-up grid, gap,
  hairline when off, `color-primary` fill plus check mark when on); with room for a help sentence, a `role="switch"`
  row (Settings). `aria-pressed` is for a lone toolbar toggle (a top-bar panel button), never a member of a labelled
  set, never in a loop or a `fieldset`; `choices-standards.test.ts` fails it. A choice this device or browser cannot take is
  not shown at all, never greyed out (Shared audio without `getDisplayMedia`, Microphone without `getUserMedia` or a
  secure context, a style without a canvas, the Halo and Swarm without the sidebar stage); a group left with nothing to
  choose is not shown, and one line says what the browser can do. `choices-standards.test.ts` fails a disabled or
  `aria-disabled` unavailable choice in the audio pane and the segmented control. During a pending request, choices
  temporarily blocked by that request expose `aria-disabled`, while the cancellation choice remains actionable.
  Choosing while another option is live switches in one step: the new one
  is asked for first, the old one stopped only once the new one runs. Segmented controls, chips and switches are
  content. The Audio pane uses Show chips (Spectrum, Aurora, Halo, Swarm), source segments where available, a status
  and privacy line, and a master switch. Workspace audio stays the default source. On a desktop with system audio
  (owner decision 2026-11-12), one extra outlined action opts into system audio or stops it, each time with its own
  consent; after an unsupported system request that action leaves and one line says why.
- **Audio visualizations follow the theme (2026-11-07).** The Spectrum rail, the Aurora, the Halo and the Heinzel swarm take every hue from the live
  theme tokens (`color-primary`, `color-ok`, `color-warn`) in any CSS colour notation, re-read the moment the mode,
  theme or its daypart variant changes; no colour is written in `web/src/audio` (`choices-standards.test.ts`). The
  Aurora is a full-window layer under the content, so the page colour is painted on `<html>` and `<body>` stays
  transparent; a painted `<body>` hides it. The Halo (a ring of rays around one Heinzel) and the Swarm (three
  Heinzels jumping, swaying and nodding to bass, mids and treble) stand in the sidebar's gap above the player and,
  like every Gnome, only while "playful" is on. They fill that gap (2026-11-12): the stage takes the room the
  navigation leaves, between 5 and 20 rem; a long navigation keeps its links and the stage stays a strip with both
  side by side, and from 12 rem the Halo takes the upper part and the Swarm the floor, drawn and moving to scale.
- **Switches show the person's choice (2026-11-08).** A provider switch (`ContributionToggle`) reads On as soon as the
  person turns it on; whether it is live is its one status line: *Turning on…*, the URL, or one short cause with
  **Try again** (or **Restart hz** when only a restart loads the change). The line is at most 60 characters in the
  person's words; `ToggleControl.status` and `problem` refuse longer text and internal state words (requested,
  applied, verified, owning, apply, intent, mapping, origin, handler, readiness). Longer text goes to the
  provider's `explanation` and detail lines, shown only under the native **Details** disclosure, never as bullets.

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
3. **One line of icons, the lead never an icon when it fits alone.** When the row has no room for the lead's words
   beside the icons, the lead takes a full-width line of its own above them; the icons keep their line and their 44px
   targets. Only a lead too wide even alone draws as an icon. Measured before paint and again on every resize, so a
   row that grows gets its one line back. Words never truncate.
4. **Dialogs answer a question**, so their row is right-aligned and every action keeps its words: Cancel (outline)
   first, the committing action (filled) last, red when it destroys something. Inline confirmations follow the same
   order. A destructive action anywhere else is a red icon that opens such a dialog.
5. **One look.** Buttons are 32px high, with a 44px minimum target on touch-capable devices and at phone viewport sizes.
   Icon-only buttons are as wide as high, with 16px icons and 6px gaps.
   Content actions are outlined; the one `primary` per container is filled; chrome and list-row actions are ghost.
   A disabled action is the same button dimmed. Pages put at most two outlined icon+words actions in their header.
6. **No links as actions.** A text link exists only inside running text (see Links below). `to=` navigates inside
   the app (a real link styled as a button), `href=` opens outside it in a new tab.
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
| Calendar pane | Now and next meeting; divider; [🎥 Join] [📅 Open calendar] [🛡 Allow changes] until the connector may write, then [Decline] [Extend] [Running late] instead of Allow |
| Calendar side pane | Day-grouped agenda, dates in sticky day headers, times in rows; divider; [📅 Today] [‹] [›] [🔧] |
| System health pane | Check summary; divider; [🩺 Open Doctor] [⟳] |
| Updates pane | Status lines, automatic-updates switch; divider; [⬇ Update now] [⟳] |
| Daemon pane (plugin) | Status lines; divider; [⚡ Restart daemon] [📄] [⏱] [⚠] (red) |
| Audio visualizer pane | Show checkbox chips, available source segments, status and master switch; desktop system audio: divider and the opt-in [Use system audio] |
| Today card | title row with [x] Dismiss; body; divider; [↗ Open] [💬] |
| Operation dialog | title row with [x]; question; divider; right: [Cancel] [Install] |
| Plugins list row | ghost [⏻] [🗑] (red); Remove opens a confirming dialog |
| Version pane (exception) | About card with the release link, release notes as prose; no action row |

Bad: an underlined "Open chat" line; an underlined "Allow ..." under the action row; two rows of buttons; a refresh button between two status lines; "Ask about it"
top-right on one card and bottom-left on the next; a wizard that puts Next before Back; a Remove that just removes.

### Links

1. **No text links in chrome.** Navigation and actions are buttons: icon-only with tooltip and `aria-label` for simple
   ones, icon and words for the lead; rows with a chevron for navigation in lists, tables and menus (the whole row is
   the target, hover tints the row, marked `data-nav-row`); a card may be its own target. Never an underlined or
   `text-primary` word.
2. **Links live only in running text:** rendered Markdown (`Prose`), release notes, help and legal pages, long content
   read top to bottom. There they are underlined `text-primary`, open outside the app in a new tab with
   `rel="noopener noreferrer"`, and an external target shows the ExternalLink glyph after the words.
3. **External URLs outside prose are icon buttons** (`PaneAction href`, ExternalLink or the provider's glyph); the
   address is never shown as a link, at most as muted text beside the button.
4. **App-like interaction:** no underline on any control; hover (`bg-accent`), focus-visible ring and pressed states on
   every button and row; 32px controls and rows, 44px on touch (`pointer-coarse`), the hit area never smaller than the
   visible control; no cursor-only affordances.
5. **Settings deep links are actions** (`PaneAction to=`, Settings2 glyph, "... settings" as words) in the row of the
   container that needs them, never inside its text. A link into Settings from a pane names one control and lands on
   it (scrolled into view, highlighted, a banner saying what the person came to do and a way back). A permission a
   feature needs is asked where the feature is, in a dialog that says exactly what will be allowed and what stays as
   it is.

### How it is enforced

- `web/src/components/PaneActions.tsx` holds the one action row (`PaneActions`, `align`, `words`), the one action
  (`PaneAction`, `primary`, `destructive`, `ghost`, `to`, `href`) and the title row with chrome (`PaneTitle chrome`).
  `FooterItem actions={…}` draws the row; plugins get the same components from the SDK and the same renderer for data.
- `FooterItem` is the one footer pane frame: content, then its divider and its one row from `actions`. A component
  inside a pane hands its actions to that row instead of drawing its own (`ContributionToggle frame`: a provider's
  switch gives its Share, Copy link, Open and Retry to the frame); a plugin's status item is data, so Core draws the
  same frame for every plugin pane.
- In development and tests, every open container is checked: a button or link outside the row, content after it, a
  second row, an action that is not a `PaneAction`, one without an icon or a name, words on more than the lead, and
  actions on more than one line fail loudly. An open footer pane also fails on an action row inside its content and
  on underlined text (`footerPaneProblems`).
- `web/src/test/links-standards.test.ts` fails on a link tag (`a`, `Link`, `NavLink`, `role="link"`) in a file that is
  not listed, on any link look (`underline`, `text-primary`, `variant="link"`) on a control, and on `data-prose-link`
  outside prose. Its list of files only shrinks; a row marked `CONVERT` leaves it with its conversion.
- `web/src/lib/paneActionsStandards.test.ts` fails on a hand-drawn `border-t`
  row, a right-aligned button row outside `PaneActions`, an icon button without a name, a raw `<button>` outside the
  listed kinds (switch, tab, menu row, nav) and an unlisted `freeform` container; in a file that draws a footer pane
  also on an unlisted own `PaneActions` row, a raw link (only the version pane's) and an `underline` class.
- The mobile browser sweep opens every footer pane, one dialog and one Today card at phone width on Chromium and
  WebKit and asserts one row, a lead with words or a declared fallback, and 44px targets. `web/e2e/footer-panes.mjs`
  mounts Core's and a plugin's footer panes at 375px on both engines and asserts the frame's row after all content, a
  divider, the same actions, no action or underlined text in the content, 44px targets and axe WCAG 2.2 AA.
