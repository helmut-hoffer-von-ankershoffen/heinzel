// Keep actions in panes on the shared primitives (design/README.md, "Actions"): no hand-made action rows or buttons inside footer panes, which draw them with PaneActions.
import { expect, it } from "vitest";
import { openingTags } from "../test/source-tags";

const screens = import.meta.glob<string>("../**/*.tsx", { query: "?raw", import: "default", eager: true });
const sources = Object.entries(screens).filter(([file]) => !file.endsWith(".test.tsx"));

const underlined = (tag: string) => /className=(?:"[^"]*|\{[^}]*)\bunderline\b/.test(tag);

// Raw buttons in files that also draw footer panes: Contributions.tsx holds the sidebar, ticker and Today widget
// controls beside FooterStatus. This only shrinks.
const RAW_BUTTONS: Record<string, number> = {
  "../components/Contributions.tsx": 4,
};

it("draws footer pane actions only with the shared title-row and bottom-row primitives", () => {
  const violations: string[] = [];
  const raw: Record<string, number> = {};
  for (const [file, source] of sources) {
    if (!source.includes("<FooterItem") || file.endsWith("/FooterItem.tsx")) continue;
    // The bottom row's divider belongs to PaneActions; a hand-drawn one drifts in spacing and placement.
    if (/\bborder-t\b(?!-)/.test(source)) violations.push(`${file}: put actions in PaneActions, not a border-t row`);
    const count = openingTags(source, "button").length;
    if (count) raw[file] = count;
  }
  expect(violations).toEqual([]);
  expect(raw).toEqual(Object.fromEntries(Object.entries(RAW_BUTTONS).filter(([, count]) => count)));
});

it("scans multi-line tags and skips arrow functions inside props", () => {
  const tags = openingTags('<button\n  onClick={() => go()}\n  className="text-primary underline"\n>', "button");
  expect(tags).toHaveLength(1);
  expect(tags.every(underlined)).toBe(true);
});

// Footer panes that may break the structure, with the reason in their `freeform` prop. Only the version pane.
const FREEFORM: Record<string, number> = {
  "../layout/StatusBar.tsx": 1,
};

it("gives every footer pane one structure: content, a divider, then one row of action buttons", () => {
  const titleActions: string[] = [];
  const freeform: Record<string, number> = {};
  for (const [file, source] of sources) {
    if (file.endsWith("/FooterItem.tsx")) continue;
    for (const tag of openingTags(source, "FooterItem")) {
      // No action in the title row or anywhere else: every action is a PaneAction in `actions`.
      if (/\saction=/.test(tag)) titleActions.push(file);
      if (/\sfreeform=/.test(tag)) {
        expect(tag, `${file}: a freeform pane says why`).toMatch(/\sfreeform="[^"]{20,}"/);
        freeform[file] = (freeform[file] ?? 0) + 1;
      }
    }
  }
  expect(titleActions).toEqual([]);
  expect(freeform).toEqual(FREEFORM);
});

it("has no withText: the first action of a row leads with words, the rest are icons", () => {
  const found = sources.filter(([, source]) => /\swithText=/.test(source)).map(([file]) => file);
  expect(found).toEqual([]);
});

// Files with a hand-drawn `border-t`: only a card's own outer edge may keep one (an action divider is PaneActions;
// separators are `<hr>` or `divide-y`). A reason per entry; this list only shrinks.
const EDGES: Record<string, { count: number; reason: string }> = {
  "../chat/ContractHistory.tsx": {
    count: 1,
    reason: "the top edge of each earlier contract version in the history list",
  },
  "../chat/DurableSession.tsx": { count: 1, reason: "the composer form's top edge under the conversation" },
  "../heinzel/PersonalHeinzels.tsx": { count: 1, reason: "the identity form's top edge under the Heinzel summary" },
  "../layout/Shell.tsx": { count: 1, reason: "the workspace panel's top edge against the page" },
  "../layout/ThemePreviews.tsx": { count: 1, reason: "the theme card's caption edge under its miniature" },
  "../sound/PlayerCard.tsx": { count: 1, reason: "the docked player card's top edge above the account row" },
};

// Files with a right-aligned button row (`justify-end`) outside PaneActions; only shrinks (PR b).
const JUSTIFY_END: Record<string, number> = {};
// Icon buttons (`<Button size="icon"`) without both an aria-label and a title; only shrinks.
const UNNAMED_ICONS: Record<string, number> = {};
// Raw `<button>`s per file: switches, tabs, menu rows and navigation, plus those PR b converts; only shrinks.
const RAW: Record<string, number> = {
  "../chat/Conversation.tsx": 1,
  "../chat/MessageInfo.tsx": 1,
  "../chat/ModelPicker.tsx": 3,
  "../chat/SessionSwitcher.tsx": 4,
  "../components/ContributionToggle.tsx": 1,
  "../components/Contributions.tsx": 4,
  "../components/FooterItem.tsx": 1,
  "../components/FooterLayout.tsx": 1,
  "../layout/AiAccessSetting.tsx": 1,
  "../layout/CalendarPanel.tsx": 1,
  "../layout/ConnectorsSection.tsx": 1,
  "../layout/InfoDialog.tsx": 2,
  "../layout/NotificationCenter.tsx": 3,
  "../layout/PluginsSection.tsx": 5,
  "../layout/SchemaValue.tsx": 1,
  "../layout/SettingControls.tsx": 1,
  "../layout/SettingsDialog.tsx": 2,
  "../layout/Sidebar.tsx": 9,
  "../layout/TerminalPanel.tsx": 8,
  "../layout/ThemePreviews.tsx": 2,
  "../layout/TimeZonePicker.tsx": 1,
  "../layout/TopBar.tsx": 2,
  "../layout/WorkspacePanels.tsx": 1,
  "../onboarding/OnboardingWizard.tsx": 1,
  // The help icon on the video is a popover's disclosure trigger, the same kind as FooterItem's (landed with #599).
  "../sound/PlayerHelp.tsx": 1,
  "../sound/SoundControls.tsx": 2,
  "../sound/StreamPlayer.tsx": 1,
};

it("keeps every action on the shared components: no new hand-drawn divider, end row, unnamed icon or raw button", () => {
  const counts = { border: {} as Record<string, number>, end: {}, unnamed: {}, raw: {} } as Record<
    "border" | "end" | "unnamed" | "raw",
    Record<string, number>
  >;
  const add = (kind: keyof typeof counts, file: string, count: number) => {
    if (count) counts[kind][file] = count;
  };
  for (const [file, source] of sources) {
    if (file.endsWith("/PaneActions.tsx") || file.includes("/ui/")) continue;
    add("border", file, (source.match(/className=(?:"[^"]*|\{[^}]*)\bborder-t\b(?!-)/g) ?? []).length);
    add("end", file, (source.match(/className=(?:"[^"]*|\{[^}]*)\bjustify-end\b/g) ?? []).length);
    add(
      "unnamed",
      file,
      openingTags(source, "Button").filter(
        (tag) => /size="icon/.test(tag) && !(/\saria-label=/.test(tag) && /\stitle=/.test(tag)),
      ).length,
    );
    add("raw", file, openingTags(source, "button").length);
  }
  expect(counts.border).toEqual(Object.fromEntries(Object.entries(EDGES).map(([file, edge]) => [file, edge.count])));
  expect(counts.end).toEqual(JUSTIFY_END);
  expect(counts.unnamed).toEqual(UNNAMED_ICONS);
  expect(counts.raw).toEqual(RAW);
});
