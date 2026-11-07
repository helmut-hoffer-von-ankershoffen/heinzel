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
- **Choices (2026-11-07).** Exclusive sets of two to four are a segmented control (`components/ui/segmented.tsx`): a
  `radiogroup` of `radio` segments on one `color-muted` track, one `color-primary` fill with
  `color-primary-foreground`, never the `color-accent` hover fill alone. A longer exclusive set (filter chips) is
  still radios. Independent sets are checkboxes: compact, a chip group (a native checkbox in a label, 2-up grid, gap,
  hairline when off, `color-primary` fill plus check mark when on); with room for a help sentence, a `role="switch"`
  row (Settings). `aria-pressed` is for a lone toolbar toggle (a top-bar panel button), never a member of a labelled
  set, never in a loop or a `fieldset`; `choices-standards.test.ts` fails it. A choice the app cannot offer at all is
  not shown (Shared audio on an iPhone); one it offers but cannot take now stays, dimmed and `aria-disabled`, with its
  reason in the tooltip and the status line. Choosing while another option is live switches in one step: the new one
  is asked for first, the old one stopped only once the new one runs. Segmented controls, chips and switches are
  content, so a pane made of them has no action row. Example: the Audio pane is Show (Spectrum, Aurora, Halo, Swarm
  chips), Listen to (Off | Microphone | Shared audio segments), status line, privacy line, master switch.
- **Audio visualizations follow the theme (2026-11-07).** The Spectrum rail, the Aurora, the Halo and the Heinzel swarm take every hue from the live
  theme tokens (`color-primary`, `color-ok`, `color-warn`) in any CSS colour notation, re-read the moment the mode,
  theme or its daypart variant changes; no colour is written in `web/src/audio` (`choices-standards.test.ts`). The
  Aurora is a full-window layer under the content, so the page colour is painted on `<html>` and `<body>` stays
  transparent; a painted `<body>` hides it. The Halo (a ring of rays around one Heinzel) and the Swarm (three
  Heinzels jumping, swaying and nodding to bass, mids and treble) stand in the sidebar's gap above the player and,
  like every Gnome, only while "playful" is on.
