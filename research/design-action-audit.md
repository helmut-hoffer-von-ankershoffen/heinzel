# Audit: actions in panes

Date: 2026-11-07. Rules: [design/README.md, "Actions in panes"](../design/README.md#actions-in-panes). Paths are
under `web/src/`. Line numbers are from the audit and drift as files change.

Each finding names the rule it breaks:

- **A** a text link is used as an action;
- **B** a full line holds one action;
- **C** the actions are not in one bottom row under the shared divider;
- **D** an icon-only control has no name;
- **E** a destructive action is not last.

## Batch 1: footer panes (this change)

| Surface | Before | Rule | After |
|---|---|---|---|
| Heinzelmännchen (`layout/DurableHeinzelStatus.tsx`) | An underlined "Create a personal Heinzel" line, an underlined "Open chat" line under a hand-drawn divider, and a small "Retry" text button | A, B, C | A chat icon in the title row ("Open chat"). The bottom row holds a filled "Create a personal Heinzel" in the empty state, or "Retry" after a failure. |
| System health (`layout/HealthStatus.tsx`) | An underlined "Open Doctor" link beside a grey text-and-icon "Refresh health checks", with no divider | A, C | A divider, then "Open Doctor" (outlined, stethoscope) and an outlined refresh icon button named "Refresh health checks" |
| Updates (`layout/UpdateStatus.tsx`) | "Verify" and "Manually update…" directly under the automatic-updates switch, with no divider | C | The switch stays in the body. The divider follows, then "Verify" (shield) and "Manually update…" (download). The copy is unchanged. |
| Tailscale sharing (`components/ContributionToggle.tsx`, through `Contributions.tsx` `FooterStatus`) | Share, Copy and Open floating between the URL and the status line | C | The status lines, switch and URL form the body. The divider follows, then Share (or Copy) and Open (external-link icon). |
| Calendar (`layout/StatusBar.tsx`) | An underlined "Open calendar" line at the bottom | A, B | A calendar icon in the title row, named "Open calendar" |
| Plugin footer and sidebar panes (`components/Contributions.tsx`) | A hand-drawn divider row. The effect button ("Open", "Run it") had no icon. | C | `PaneActions`. Every action has an icon: arrow for open, play for run. |
| Version (`layout/StatusBar.tsx`) | A GitHub icon at the right of the title, named, with a tooltip | — | Compliant |
| Daemon (plugin pane) | Divider, then Logs / Pause / Uninstall | — | Compliant (the model) |

## Batch 2: the remaining footer panes and panels

| Surface | Now | Rule | Fix |
|---|---|---|---|
| Plugins pending (`layout/PluginsPending.tsx:65-99`) | A lone "Restart…" button mid-pane. "Check again" and "Stop checking" sit in a separate row. | C | One `PaneActions` row. Cancel comes before confirm. |
| Audio (`audio/AudioVisualizer.tsx:205`) | A full-width Start/Stop button | B | A title-row icon (Play/Square) named from its state |
| Calendar panel (`layout/CalendarPanel.tsx`) | Underlined "Notification settings", "Set up reminder worker", "Remove reminder worker" (twice), "Connect a calendar…" and "Join meeting" | A, B, C, E | A settings icon in the title row. Reminder actions go in one bottom row with Remove last. The empty state gets a primary "Connect a calendar". "Join meeting" becomes a video icon. |
| Notification centre (`layout/NotificationCenter.tsx:153`) | A text-only "Mark read" in the header | — | Make it an icon (CheckCheck) named "Mark all read" |
| Today widgets (`components/Contributions.tsx` `WidgetBody`/`WidgetCard`, `pages/Home.tsx`) | The widget actions row and the Ask/Dismiss row are separate, with no divider. List items are hover-underlined buttons. | C, A | One row under the divider. List items get a chevron instead of an underline. |
| Today notifications (`notifications/TodayNotifications.tsx:45`) | Underlined "All" | A, B | An arrow icon in the heading row |

## Batch 3: settings, dialogs, chat and pages

| Surface | Now | Rule | Fix |
|---|---|---|---|
| Daemon settings (`layout/DaemonSettings.tsx:57-86`) | Pause, Restart, Uninstall, then a ghost "Refresh", with no divider | C, E | A refresh icon in the title row. The divider follows, then Uninstall last. |
| Web server settings (`layout/WebServerSettings.tsx`) | "Restart" on its own line. The confirm row puts the primary before Cancel. | B | A title-row icon that opens the confirmation. The confirm row is Cancel, then primary. |
| Plugins settings (`layout/PluginsSection.tsx:506, 588, 672`) | "Resolve" on its own line per receipt. One underlined link. | A, B | Fold into the receipt's bottom row |
| Settings files (`layout/SettingsDialog.tsx:454`) | "Show in Finder" with text, one per row | B | A folder icon per row, named |
| Operation dialog (`components/OperationDialog.tsx:156`) | Cancel, then primary, with no divider | C | Add the divider via `PaneActions` |
| Chat proposal card (`chat/Conversation.tsx:73`) | Authorize, Confirm, Decline, with no divider | C | `PaneActions`. Decline is already last. |
| Durable session (`chat/DurableSession.tsx`) | A text "Close" in the header. Review, Propose, Discard, Recover and Acknowledge are separate lone buttons. Two inline "Daemon settings" links. | B, C, D | An X icon named "Close". One bottom row. The inline links are prose: mark them `data-prose-link`. |
| Skill drafts and sharing (`chat/SkillDraftPanel.tsx`, `chat/SkillSharingPanel.tsx`) | Columns of full-width "Load…", "Open draft …" and "More…" buttons | B, C | A list with an open icon per item. Load and More go in a bottom row. |
| Activity panels (`chat/CapturedWriteActivity.tsx`, `chat/ReleaseDraftActivity.tsx`, `chat/NotificationPanel.tsx`) | "Refresh" on its own line, or in a row with no divider | B, C | A refresh icon in the title row |
| Personal Heinzels (`heinzel/PersonalHeinzels.tsx:232, 563`) | "Create" alone on a line. Save, Discard, Reload, then a separate "Open schedule". | B, C | A plus icon in the section title. One bottom row. |
| Workflow node panel (`pages/workflow/NodePanel.tsx:117, 156`) | Underlined "Open ↗", "Open contract" | A | A title-row icon (external link, file) |
| Profile (`pages/Profile.tsx:35, 63`) | Underlined "Back" and "Not set" | A, B | "Back" becomes an arrow icon. "Not set" is a value: mark it as prose. |
| Contracts, Inventory, Personal artifacts, Heinzelmännchen pages | One underlined action link each | A | A button, or `data-prose-link` when it is part of a sentence |
| Page headers (`components/common.tsx` `Page`) | Have an `actions` slot at the top right | — | Compliant. "Re-run", "Discover", "Search" and "Copy Mermaid" may become icon-only. |
| Top bar, sidebar, chat and terminal headers, plugin header actions, ticker | Icon-only, named, with tooltips | — | Compliant. The top bar's "Open calendar" literal moves to `t()`. |

The pending counts in `web/src/lib/pane-actions-standards.test.ts` track batches 2 and 3. Each fixed file leaves the
list in the same pull request.

## Proposal: mask the Tailscale share token

The Tailscale pane shows the share URL in full, including `#token=…`. That fragment is the bearer token that grants
access, and the "Open" link carries the same URL. This change leaves the behaviour as it is. Proposed for a separate,
reviewed change:

- Show only the origin (`https://name.tailnet.ts.net/`) and a masked token (`#token=••••`). Add an eye icon,
  named "Show access token", to reveal it for 30 seconds.
- Copy and Share keep the full URL, since sending it is their job. After copying, the body says "Copied, including
  the access token", so nobody pastes it into a public place by surprise.
- Decide separately whether "Open" should carry the token. It puts the token in the new tab's address bar and
  history. Opening the bare origin and signing in there may be the safer default.
- Screenshots and screen sharing are the main leak paths. Masking by default closes them without making the pane
  harder to use.

## Space economy (to be measured)

The principle "Content in focus, no screen space wasted" ([design/README.md](../design/README.md)) adds a column to
this audit: the share of the viewport that is content rather than chrome, per screen, at 1440x900 and 390x844. It is
not measured yet. The next audit pull request measures it from the mobile sweep's screenshots (content box area over
viewport area) and adds the column to the tables above, worst first.

Related work in flight elsewhere, not duplicated here: compact Today actions, the sidebar gap (#563), the composer
cost line, overlay scrollbars (#566) and the brand header.
