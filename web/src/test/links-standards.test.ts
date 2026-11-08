// No text links in chrome (design/README.md, "Links"; research/no-text-links-2026-11-08.md): navigation and actions
// are buttons or rows with a chevron; a link exists only in rendered prose or long content. External targets are
// `PaneAction href`. This list only shrinks: a row marked CONVERT leaves it with its conversion, no file may join.
import { expect, it } from "vitest";
import { openingTags, tagsWithAttribute } from "./source-tags";

const screens = import.meta.glob<string>("../**/*.tsx", { query: "?raw", import: "default", eager: true });
const sources = Object.entries(screens).filter(([file]) => !/\.test\.tsx?$/.test(file) && !file.includes("/test/"));
const LINK_TAGS = ["a", "Link", "NavLink"];

const linkTags = (source: string) => [
  ...LINK_TAGS.flatMap((name) => openingTags(source, name)),
  ...tagsWithAttribute(source, 'role="link"').filter((tag) => !/^<(a|Link|NavLink)[\s>]/.test(tag)),
];

const LINKS: Record<string, { count: number; reason: string }> = {
  "../components/PaneActions.tsx": {
    count: 2,
    reason: "KEEP: the link inside PaneAction to= and href=: the one place a link is a button",
  },
  "../components/Prose.tsx": { count: 3, reason: "KEEP: rendered Markdown: links and images in running text" },
  "../layout/Shell.tsx": { count: 1, reason: "KEEP: the skip link to #content, visually hidden (accessibility)" },
  "../layout/Sidebar.tsx": { count: 12, reason: "KEEP: navigation and account-menu rows, the whole row is the target" },
  "../pages/graph/TeamNodes.tsx": { count: 3, reason: "KEEP: the whole graph card is the hit target, no underline" },
  "../notifications/TodayNotifications.tsx": {
    count: 1,
    reason: "CONVERT: a notice row; mark data-nav-row instead of data-prose-link (brief C)",
  },
  "../pages/Contracts.tsx": {
    count: 1,
    reason: "CONVERT: contract title is a text link; make the row the target with a chevron (brief C)",
  },
  "../pages/Heinzelmaennchen.tsx": {
    count: 1,
    reason: "CONVERT: name cell is a text link; row navigates with a chevron (brief C)",
  },
  "../chat/DurableSession.tsx": {
    count: 2,
    reason:
      "CONVERT: Daemon settings Button asChild Link in a status line; PaneAction in the schedule card row (brief E)",
  },
  "../components/SettingsShortcut.tsx": {
    count: 1,
    reason: "CONVERT: Link styled as an icon button; PaneAction ghost icon to= (brief E)",
  },
  "../layout/PluginsSection.tsx": {
    count: 2,
    reason: "CONVERT: First party chip is a link and the source address is a link; Badge and muted text (brief D)",
  },
  "../layout/StatusBar.tsx": {
    count: 1,
    reason: "CONVERT: hand-made GitHub icon link in the version pane; PaneAction ghost href (brief D)",
  },
  "../onboarding/OnboardingWizard.tsx": {
    count: 1,
    reason: "CONVERT: Open settings link; names one control and lands on it (brief H)",
  },
  "../pages/Profile.tsx": {
    count: 2,
    reason: "CONVERT: Not set button as a value and NotFound Back link; PaneActions (brief E)",
  },
};

it("has links only in the listed files, and the counts only fall", () => {
  const found: Record<string, number> = {};
  for (const [file, source] of sources) {
    const count = linkTags(source).length;
    if (count) found[file] = count;
  }
  expect(found).toEqual(Object.fromEntries(Object.entries(LINKS).map(([file, entry]) => [file, entry.count])));
  for (const [file, entry] of Object.entries(LINKS)) expect(entry.reason, file).toMatch(/^(CONVERT|KEEP): \S/);
});

it("gives no control the look of a link", () => {
  const look =
    /className=(?:"[^"]*|\{[^}]*)(?:\bunderline\b|\bhover:underline\b|\bunderline-offset-|\bdecoration-|\btext-primary(?![-/\w]))/;
  const found: string[] = [];
  for (const [file, source] of sources) {
    if (file.endsWith("/Prose.tsx")) continue; // rendered Markdown: links in running text are underlined text-primary
    for (const name of ["a", "Link", "NavLink", "button", "Button"])
      // A tag still marked data-prose-link is on the PROSE_MARKERS list and leaves with its conversion.
      for (const tag of openingTags(source, name))
        if ((look.test(tag) && !tag.includes("data-prose-link")) || /variant="link"/.test(tag))
          found.push(`${file}: ${tag.slice(0, 60).replace(/\s+/g, " ")}`);
  }
  expect(found).toEqual([]);
  const button = sources.find(([file]) => file.endsWith("/components/ui/button.tsx"))?.[1] ?? "";
  expect(button).not.toMatch(/\blink:/);
});

// Navigation rows inside lists and cards mark themselves `data-nav-row`; a per-file allowlist with a reason.
const NAV_ROWS: Record<string, string> = {};
// Files that still mark a row or fact `data-prose-link`; each leaves with its conversion. Only shrinks.
const PROSE_MARKERS: Record<string, string> = {
  "../layout/PluginsSection.tsx": "CONVERT: source address (brief D)",
  "../notifications/TodayNotifications.tsx": "CONVERT: notice row (brief C)",
  "../pages/Contracts.tsx": "CONVERT: contract title (brief C)",
  "../pages/Heinzelmaennchen.tsx": "CONVERT: Heinzel name (brief C)",
};

it("marks prose links only in prose, and navigation rows only where listed", () => {
  const prose = sources.filter(([, source]) => source.includes("data-prose-link")).map(([file]) => file);
  expect(prose.sort()).toEqual(["../components/PaneActions.tsx", ...Object.keys(PROSE_MARKERS)].sort());
  for (const [file, reason] of Object.entries(PROSE_MARKERS)) expect(reason, file).toMatch(/^(CONVERT|KEEP): \S/);
  const rows = sources.filter(([, source]) => source.includes("data-nav-row")).map(([file]) => file);
  expect(rows.filter((file) => !(file in NAV_ROWS) && !file.endsWith("/PaneActions.tsx"))).toEqual([]);
});

it("scans multi-line tags, arrow functions in props, and ignores <link> and <image>", () => {
  const source =
    '<Link\n  to="/x"\n  onClick={() => go()}\n  className="underline"\n>x</Link><link rel="stylesheet" /><image href="a" />';
  expect(linkTags(source)).toHaveLength(1);
  expect(linkTags('<div role="link" />')).toHaveLength(1);
});
