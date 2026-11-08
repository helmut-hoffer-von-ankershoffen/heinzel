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
