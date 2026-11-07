// Keep actions in panes on the shared primitives (design/README.md, "Actions in panes"): no text links used as
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
  "../components/Contributions.tsx": 1,
  "../layout/CalendarPanel.tsx": 5,
  "../layout/PluginsSection.tsx": 1,
  "../notifications/TodayNotifications.tsx": 2,
  "../pages/Contracts.tsx": 1,
  "../pages/Heinzelmaennchen.tsx": 1,
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
