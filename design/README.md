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
