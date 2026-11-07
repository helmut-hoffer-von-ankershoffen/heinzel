# Design system

This is the one source for the rules of how Heinzelmännchen looks and behaves. Values (colours, radii, fonts) live
in [`tokens.json`](tokens.json); a drift test keeps the app and the Pages site on them. Names and wording live in
[docs/BRANDING.md](../docs/BRANDING.md). Layout on phones is in
[Mobile UI](../docs/CODING_STANDARDS.md#mobile-ui).

## Content in focus, no screen space wasted

The content the person came for gets the space; everything else earns its place. This is a first-class principle,
checked in review like the brand rules:

1. **Never repeat a title.** When a header selector already names the page, the page has no second heading saying
   the same; keep one.
2. **One action is one icon button** in the header zone; several are one bottom row under a divider
   ([Actions in panes](#actions-in-panes)).
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

## Actions in panes

A pane is any bounded surface with a title: a footer pane, a sidebar pane, a popover, a settings section, a panel
or a dialog. Every pane is built the same way, from top to bottom:

```text
┌──────────────────────────────────────────┐
│ Title                         [icon] (1) │  title row: at most one icon-only action, at the right
├──────────────────────────────────────────┤
│ Body: status, facts, lists, switches,    │  body: what the pane says, and settings that belong to it
│ messages, errors ("Copied", "Failed")    │  (switches stay here, beside the text they change)
├──────────────────────────────────────────┤  divider, drawn by PaneActions, never by hand
│ [▶ Primary] [⟳ Other] [⏻ Other] [🗑 Bad] │  bottom action row: buttons side by side, wrapping (2)
└──────────────────────────────────────────┘
```

(1) `PaneIconAction`, or `FooterItem action={…}`. (2) `PaneActions` with `PaneAction` children, or
`FooterItem actions={…}`.

### Which control

| The pane has | Control | Where |
|---|---|---|
| Exactly one action, in every state, that is not primary, not destructive and has an obvious icon (open, refresh, copy, settings) | Icon-only ghost button, `PaneIconAction`, with a name such as "Open chat" | Title row, right |
| Two or more actions, or more expected soon | Outlined buttons with icon and text, `PaneAction` | Bottom row |
| The action the pane exists for: an empty state ("Create a personal Heinzel"), "Retry" after a failure | The one filled button, `PaneAction primary`, with text | Bottom row, first |
| A destructive action (Uninstall, Remove, Delete) | `PaneAction destructive`, always with text, asks to confirm | Bottom row, last |
| A well-known verb beside labelled buttons (refresh, copy) | `PaneAction iconOnly`, same height as its neighbours | Bottom row |
| A reference inside a sentence, help text, docs, legal pages | Inline text link (underlined), marked `data-prose-link` when it is not in `Prose` | In the sentence |
| A place outside the app | `href=` on the action: it opens in a new tab, and its icon says so (`ExternalLink`) | Where the table above puts it |

### Rules

1. **No text links as actions.** An action is a button. A text link is only for a reference inside prose.
2. **No full line for one action.** One simple action is an icon in the title row. "Open chat" on its own line is wrong.
3. **One bottom row.** All other actions go in one row under a divider at the end of the pane. Nothing floats between
   the body's lines. Status and error text stays above the divider, never between buttons.
4. **The divider comes from `PaneActions`.** A pane never draws its own `border-t` row. `PaneActions` draws nothing,
   not even the divider, when every action is hidden.
5. **Only `PaneAction` in the row.** Mixed rows (a text link beside a button, a small grey text button beside an
   outlined one) are impossible: `PaneActions` refuses any other child in development and tests.
6. **Order.** Primary first, then navigation ("Open Doctor"), then commands by how often they are used, then
   destructive last.
7. **One primary per pane**, at most. When nothing is primary, every action is outlined.
8. **Every action has an icon** from the lucide set already in use; text is short: verb plus object.
9. **Icon-only actions have a name.** `label` (or the text of an `iconOnly` action) becomes both the accessible name
   and the tooltip. Name the verb and the object: "Open chat", not "Open".
10. **Size.** Actions are at least 32px high (`sm`), above WCAG 2.2's 24px minimum; on touch screens
    (`pointer: coarse`) they grow to 44px. Rows wrap; they never scroll sideways.
11. **Disabled looks disabled.** A disabled action is the same button, dimmed, not grey text. While it runs, it says
    so ("Creating…") and keeps its place.
12. **Overflow.** Up to three actions show; from four on, show the two most used and put the rest behind a
    "More actions" menu. Destructive actions stay visible or come last in that menu, after a separator.
13. **Navigation stays navigation.** Inside the app, `to=` renders a real link styled as a button, so the URL, middle
    click and history keep working. Never `onClick={() => navigate(…)}` for plain navigation.
14. **Destructive actions confirm** in a dialog that names the consequence on its button ("Uninstall"), not "OK".

### Good and bad

| Bad | Good |
|---|---|
| Heinzels pane: "No persisted Heinzels yet.", then an underlined line "Create a personal Heinzel", then a line "Open chat" | Title row "Heinzelmännchen" with a chat icon at the right ("Open chat"); body "No persisted Heinzels yet."; divider; filled "+ Create a personal Heinzel" |
| System health pane: underlined "Open Doctor" beside grey text "Refresh health checks", no divider | Divider, then "Open Doctor" (outlined, stethoscope) and an outlined refresh icon named "Refresh health checks" |
| Tailscale pane: Share and Open buttons between the URL and the status line | Status lines, switch and URL in the body; divider; Share (or Copy) and Open in the bottom row |
| Updates pane: "Manually update…" directly under the automatic-updates switch | Switch in the body; divider; "Manually update…" with its download icon |
| Calendar pane: an underlined "Open calendar" line at the bottom | A calendar icon at the right of the title, named "Open calendar" |
| Daemon pane (good already): divider, then Logs / Pause / Uninstall | Keep; Uninstall stays last |

### How it is enforced

- `web/src/components/PaneActions.tsx` holds `PaneActions`, `PaneAction`, `PaneIconAction` and `PaneTitle`.
  `FooterItem` takes `action` and `actions` and draws them in their places; plugin panes declared as data get them
  through the same renderer.
- `web/src/lib/pane-actions-standards.test.ts` fails on a new underlined text link used as an action anywhere in the
  app, on a hand-drawn `border-t` row in a footer pane, and on new raw buttons in footer-pane files. Its lists of
  files still to convert only shrink.
- The audit of every pane, and the order they are converted in, is in
  [research/design-action-audit.md](../research/design-action-audit.md).
- What a test cannot judge (whether an icon is clear, whether wording is good, real wrapping and target size at
  phone widths) is checked in review and in the mobile browser sweep's screenshots.
