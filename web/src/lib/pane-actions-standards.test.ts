// Keep actions in panes on the shared primitives (design/README.md, "Actions"): no text links used as
// actions, and no hand-made action rows or buttons inside footer panes, which draw them with PaneActions.
import { expect, it } from "vitest";

const screens = import.meta.glob<string>("../**/*.tsx", { query: "?raw", import: "default", eager: true });
const sources = Object.entries(screens).filter(([file]) => !file.endsWith(".test.tsx"));

/** Every opening tag of `name` in `source`, up to its closing `>` outside braces (so `=>` in props is skipped). */
function openingTags(source: string, name: string): string[] {
  const tags: string[] = [];
  const start = new RegExp(`<${name}(?=[\\s>])`, "g");
  for (let match = start.exec(source); match; match = start.exec(source)) {
    let depth = 0;
    let end = match.index;
    for (; end < source.length; end++) {
      const char = source[end];
      if (char === "{") depth++;
      else if (char === "}") depth--;
      else if (char === ">" && depth === 0 && source[end - 1] !== "=") break;
    }
    tags.push(source.slice(match.index, end + 1));
  }
  return tags;
}

const underlined = (tag: string) => /className=(?:"[^"]*|\{[^}]*)\bunderline\b/.test(tag);

// Text links still used as actions, to be converted by the rollout in research/design-action-audit.md. This list
// only shrinks: a fixed file must leave it, and no new file may join it. Inline prose links take `data-prose-link`.
const PENDING: Record<string, number> = {
  "../chat/DurableSession.tsx": 2,
  "../layout/PluginsSection.tsx": 1,
  "../pages/Contracts.tsx": 1,
  "../pages/Inventory.tsx": 1,
  "../pages/PersonalArtifacts.tsx": 1,
  "../pages/Profile.tsx": 2,
  "../pages/workflow/NodePanel.tsx": 2,
};

it("uses buttons, not underlined text links, for actions", () => {
  const found: Record<string, number> = {};
  for (const [file, source] of sources) {
    if (file.endsWith("/Prose.tsx")) continue; // rendered Markdown: links inside prose are the right control
    const count = ["button", "Link", "a"]
      .flatMap((name) => openingTags(source, name))
      .filter((tag) => underlined(tag) && !tag.includes("data-prose-link")).length;
    if (count) found[file] = count;
  }
  expect(found).toEqual(PENDING);
});

// Raw buttons in files that also draw footer panes: Contributions.tsx holds the sidebar, ticker and Today widget
// controls beside FooterStatus. Like PENDING, this only shrinks.
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

// Files that still draw a divider by hand (`border-t`) in a container instead of PaneActions; only shrinks (PR b).
const BORDER_T: Record<string, number> = {
  "../chat/ContractHistory.tsx": 1,
  "../chat/DurableSession.tsx": 1,
  "../heinzel/PersonalHeinzels.tsx": 1,
  "../layout/DaemonSettings.tsx": 1,
  "../layout/Shell.tsx": 1,
  "../layout/ThemePreviews.tsx": 1,
  "../onboarding/OnboardingWizard.tsx": 1,
  "../pages/Doctor.tsx": 1,
  "../pages/Organisation.tsx": 1,
};
// Files with a right-aligned button row (`justify-end`) outside PaneActions; only shrinks (PR b).
const JUSTIFY_END: Record<string, number> = {
  "../components/CommandDialog.tsx": 1,
  "../components/OperationDialog.tsx": 4,
  "../layout/PluginsSection.tsx": 1,
};
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
  "../layout/ConnectorsSection.tsx": 2,
  "../layout/InfoDialog.tsx": 3,
  "../layout/NotificationCenter.tsx": 3,
  "../layout/PluginsSection.tsx": 5,
  "../layout/SchemaValue.tsx": 4,
  "../layout/SettingControls.tsx": 5,
  "../layout/SettingsDialog.tsx": 3,
  "../layout/Sidebar.tsx": 7,
  "../layout/TerminalPanel.tsx": 8,
  "../layout/ThemePreviews.tsx": 2,
  "../layout/TimeZonePicker.tsx": 1,
  "../layout/TopBar.tsx": 2,
  "../layout/WorkspacePanels.tsx": 1,
  "../onboarding/OnboardingWizard.tsx": 2,
  "../sound/SoundControls.tsx": 2,
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
  expect(counts.border).toEqual(BORDER_T);
  expect(counts.end).toEqual(JUSTIFY_END);
  expect(counts.unnamed).toEqual(UNNAMED_ICONS);
  expect(counts.raw).toEqual(RAW);
});
