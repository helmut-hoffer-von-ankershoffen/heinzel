// Where each part of Settings lives: every setting in exactly one obvious place, by what people think about (docs/
// SETTINGS_UX.md), not by the module that declares it. Preferences are how the app looks and tells you things;
// Customize is the AI, the tools it connects to, plugins and skills; Organisation is you and your team; This computer
// is the daemon, the files, what comes from Claude and updates. A module gets its own entry only where it adds a page
// of its own; installed providers declare their own pages and semantic icons.
import {
  Bell,
  Cpu,
  Download,
  FileCog,
  Globe,
  KeyRound,
  type LucideIcon,
  Palette,
  Plug,
  Puzzle,
  RefreshCw,
  Server,
  Sparkles,
  UserRound,
  Users,
  WandSparkles,
} from "lucide-react";
import type { SchemaNavEntry, SchemaPluginInfo } from "@/api/client";
import { ICONS } from "@/components/Contributions";

export const FILES = "files";
export const FROM_CLAUDE_PAGE = "from-claude";
export const PLUGINS = "plugins";
export const CONNECTORS = "connectors";
/** The `[capabilities]` table: which adapter serves which capability; shown on the Connectors page. */
export const BINDINGS = "capabilities";

/** `system.runner` → `system`: the left side lists modules; their sub-sections are headings on the right. */
export const group = (section: string) => section.split(".")[0] ?? section;

const BRANDS: Record<string, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  hz: "hz",
};
/** Words written in capitals wherever they appear in a name: `ai access` → `AI access`, never `Ai access`. */
const ACRONYMS: Record<string, string> = {
  acme: "ACME",
  ai: "AI",
  api: "API",
  cli: "CLI",
  mcp: "MCP",
  rest: "REST",
  ui: "UI",
  url: "URL",
  usd: "USD",
  github: "GitHub",
  gitlab: "GitLab",
};

/** Words in sentence case with their capitals kept: `ai_access` → `AI access`, `ui_port` → `UI port`. */
export function inWords(name: string): string {
  const words = name
    .split(/[-_ ]+/)
    .filter(Boolean)
    .map((word) => ACRONYMS[word.toLowerCase()] ?? word);
  const [first = "", ...rest] = words;
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(" ");
}

/**
 * A plugin's name as people write it, in Title Case: known acronyms and brands first (`ACME`, `AI`, `GitHub`), else the
 * casing its summary gives the word, else the word capitalised; a `heinzel-` prefix is dropped (`heinzel-slack` is `Slack`).
 */
export function displayName(rawName: string, summary = ""): string {
  const name = rawName.replace(/^heinzel-/, "");
  if (name.toLowerCase() in BRANDS) return BRANDS[name.toLowerCase()] as string;
  const words = name.split(/[-_ ]+/).filter(Boolean);
  return words
    .map((word) => {
      const known = ACRONYMS[word.toLowerCase()];
      if (known) return known;
      const found = summary.match(new RegExp(`\\b${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i"))?.[0];
      if (found && found !== found.toLowerCase()) return found;
      return word.charAt(0).toUpperCase() + word.slice(1);
    })
    .join(" ");
}

/** A plugin's summary without the "…: a Heinzelmännchen plugin." its template ends it with; the header says so. */
export const plainSummary = (summary: string) =>
  summary.replace(/\s*[:;,–—-]\s*an? Heinzelmännchen plugin\.?\s*$/i, ".");

export type SettingsEntry = {
  id: string;
  title: string;
  plugin?: SchemaPluginInfo;
};

export type Heading = "preferences" | "customize" | "organisation" | "computer";
/** A page of Settings: its nav entry and the setting sections it owns (a prefix owns its sub-sections). */
export type Page = {
  id: string;
  title: string;
  icon: LucideIcon;
  heading: Heading;
  owns: string[];
  /** Words that find the page by what is on it, besides its settings (search finds every setting, schema or not). */
  keywords?: string;
  plugin?: SchemaPluginInfo;
  module?: string;
  provider?: string | null;
  /** The one status line of a layer page: "Site layer of Acme". */
  status?: string;
};

export const PAGES: Page[] = [
  {
    id: "appearance",
    title: "Appearance",
    icon: Palette,
    heading: "preferences",
    owns: ["system.theme", "system.sound"],
    keywords: "mode light dark system theme colours sound volume expert mode",
  },
  {
    id: "notifications",
    title: "Notifications",
    icon: Bell,
    heading: "preferences",
    owns: ["system.notify"],
    keywords: "notices alerts desktop browser",
  },
  {
    id: "ai",
    title: "AI",
    icon: Sparkles,
    heading: "customize",
    owns: ["ai", "system.runner"],
    keywords: "access subscription models budget chat tools claude codex",
  },
  {
    id: CONNECTORS,
    title: "Connectors",
    icon: Plug,
    heading: "customize",
    owns: [],
  },
  {
    id: PLUGINS,
    title: "Plugins",
    icon: Puzzle,
    heading: "customize",
    owns: [],
  },
  {
    id: "skills",
    title: "Skills",
    icon: WandSparkles,
    heading: "customize",
    owns: ["system.skill", "system.agent"],
    keywords: "agent skills folders",
  },
  {
    id: "you",
    title: "You",
    icon: UserRound,
    heading: "organisation",
    owns: ["org", "report"],
    keywords: "handle role profile",
  },
  {
    id: "team",
    title: "Team",
    icon: Users,
    heading: "organisation",
    owns: ["team", "heinzel", "graph"],
    keywords: "repository organisation",
  },
  {
    id: "credentials",
    title: "Credentials",
    icon: KeyRound,
    heading: "computer",
    owns: ["system.credential"],
    keywords: "credentials secrets keychain tokens",
  },
  {
    id: "web-server",
    title: "Web server",
    icon: Globe,
    heading: "computer",
    owns: [],
    keywords: "restart reconnect browser ui service",
  },
  {
    id: "daemon",
    title: "Daemon",
    icon: Server,
    heading: "computer",
    owns: ["system.daemon"],
    keywords: "background service",
  },
  {
    id: FILES,
    title: "Configuration files",
    icon: FileCog,
    heading: "computer",
    owns: ["hz"],
    keywords: "folders data cache logs",
  },
  {
    id: FROM_CLAUDE_PAGE,
    title: "From Claude",
    icon: Download,
    heading: "computer",
    owns: [],
  },
  {
    id: "updates",
    title: "Updates",
    icon: RefreshCw,
    heading: "computer",
    // The automatic-update settings are kernel settings, but people look for them with the other update choices.
    owns: [
      "system.changelog",
      "system.plugin",
      "hz.auto_update",
      "hz.auto_update_min_age_hours",
      "hz.auto_update_plugin_min_age_hours",
    ],
    keywords: "release notes github token automatic updates",
  },
  {
    id: "system",
    title: "System",
    icon: Cpu,
    heading: "computer",
    owns: ["system"],
    keywords: "directory",
  },
];

/** Old links by what they meant: `?settings=hz` was General, whose appearance and AI access now live elsewhere. */
const ALIASES: Record<string, string> = {
  hz: "appearance",
  org: "you",
  heinzel: "team",
  runner: "ai",
};

const owns = (page: Page, section: string) => page.owns.some((own) => section === own || section.startsWith(`${own}.`));

/** Legacy display providers remain preferences; identify their capability/kind, never their package name. */
function preferenceIcon(plugin: SchemaPluginInfo | undefined): LucideIcon | undefined {
  if (plugin?.capabilities?.some((capability) => capability.split(":").at(-1) === "weather")) return ICONS["cloud-sun"];
  if (plugin?.kinds?.includes("theme") || plugin?.themes?.length) return Palette;
  return undefined;
}

/** The page that owns a setting section: the longest matching prefix, else a plugin module's own page, else System. */
export function pageOf(section: string, pages: Page[]): Page | undefined {
  let best: Page | undefined;
  let length = -1;
  for (const page of pages) {
    for (const own of page.owns) {
      if ((section === own || section.startsWith(`${own}.`)) && own.length > length) {
        best = page;
        length = own.length;
      }
    }
  }
  return best ?? pages.find((page) => page.owns.length === 0 && page.id === group(section));
}

/** The page of one setting: a page that owns exactly `section.key` (a single kernel setting placed by what it is
 * about), else the page of its section. */
export function settingPage(section: string, key: string, pages: Page[]): Page | undefined {
  const exact = `${section}.${key}`;
  return pages.find((page) => page.owns.includes(exact)) ?? pageOf(section, pages);
}

export type SettingsNav = {
  /** Every page, by heading order: the fixed ones, then a page for each plugin module hz doesn't know. */
  pages: Page[];
  /** Plugins that bring adapters, each with the setting groups of its modules. */
  connectors: (SettingsEntry & {
    plugin: SchemaPluginInfo;
    groups: string[];
  })[];
};

/** A section id from a link (`?settings=`), as the page it opens: a page's id, a section it owns, or an old name. */
export function resolvePage(id: string | null | undefined, pages: Page[]): string | undefined {
  if (!id) return undefined;
  if (pages.some((page) => page.id === id)) return id;
  const alias = ALIASES[id];
  if (alias && pages.some((page) => page.id === alias)) return alias;
  return pageOf(id, pages)?.id;
}

/** The sidebar group a plugin's page sits under: the one place that decides it (today the group it declares). */
function placement(entry: SchemaNavEntry, plugin: SchemaPluginInfo | undefined): Heading {
  const group = entry.settings?.group ?? "preferences";
  // A layer is part of the organisation; the server says so, and an older server's undeclared page lands there too.
  return !entry.declared && group === "preferences" && plugin?.layers?.length ? "organisation" : group;
}

const COLLATOR = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });

/**
 * Every sidebar group lists its entries alphabetically by display name (case-insensitive, numeric-aware), core and
 * plugin pages together; no group pins an entry first. The single place that orders the sidebar. Stable.
 */
export function sortedWithinGroups<T extends { title: string; heading: Heading }>(pages: T[]): T[] {
  const headings = [...new Set(pages.map((page) => page.heading))];
  return headings.flatMap((heading) =>
    pages.filter((page) => page.heading === heading).sort((a, b) => COLLATOR.compare(a.title, b.title)),
  );
}

/** Sorts the setting groups into pages, Connectors by the plugin that provides each module. */
export function settingsNav(
  groups: string[],
  plugins: SchemaPluginInfo[],
  navigation: SchemaNavEntry[] = [],
): SettingsNav {
  const owner = new Map<string, SchemaPluginInfo>();
  for (const plugin of plugins) for (const module of plugin.modules) owner.set(group(module), plugin);
  const connects = (plugin: SchemaPluginInfo | undefined) => (plugin?.capabilities ?? []).length > 0;
  const of = (plugin: SchemaPluginInfo) => groups.filter((name) => owner.get(name) === plugin);
  const byTitle = (a: SettingsEntry, b: SettingsEntry) => a.title.localeCompare(b.title);
  const layers = plugins.flatMap((item) => item.layers ?? []);
  const extra: Page[] = navigation.flatMap((entry) => {
    if (!entry.settings || entry.problem) return [];
    const plugin = plugins.find((item) => item.distribution === entry.plugin || item.name === entry.plugin);
    if (plugin && (!plugin.enabled || plugin.error)) return [];
    const layer = plugin?.layers?.[0];
    // Legacy provider metadata must not move host settings out of their existing conceptual pages.
    if (
      !entry.declared &&
      ((connects(plugin) && !preferenceIcon(plugin) && !layer) ||
        entry.settings.sections.every((section) => PAGES.some((page) => owns(page, section))))
    )
      return [];
    return [
      {
        id: entry.id,
        title: entry.title,
        status: layer ? layerStatus(layer, layers) : undefined,
        icon: !entry.declared && entry.icon === "code" ? (preferenceIcon(plugin) ?? Puzzle) : ICONS[entry.icon],
        heading: placement(entry, plugin),
        owns: entry.settings.sections,
        plugin,
        module: entry.module,
        provider: entry.plugin,
      },
    ];
  });
  const pages = sortedWithinGroups(
    [...PAGES, ...extra].map((page) => ({ ...page, plugin: page.plugin ?? pluginOfPage(page, owner) })),
  );
  return {
    pages,
    connectors: plugins
      .filter(connects)
      .map((plugin) => ({
        id: plugin.name,
        title: displayName(plugin.name, plugin.summary),
        plugin,
        groups: of(plugin).filter((section) => !extra.some((page) => owns(page, section))),
      }))
      .sort(byTitle),
  };
}

type Layer = NonNullable<SchemaPluginInfo["layers"]>[number];

/** "Site layer of Acme": its label, the layer above it, and for a team the site it is at; at most 60 characters. */
function layerStatus(layer: Layer, all: Layer[]): string {
  const name = (module: string) => displayName(module).replace(/ [a-z]/g, (letters) => letters.toUpperCase());
  const label = layer.label ? `${inWords(layer.label)} layer` : "Layer";
  const above = layer.parent;
  const site =
    layer.label === "team" && layer.site && all.some((item) => item.module === layer.site) ? layer.site : null;
  const text = `${label}${above ? ` of ${name(above)}` : ""}${site ? `, at ${name(site)}` : ""}`;
  return text.length <= 60 ? text : `${text.slice(0, 59)}…`;
}

function pluginOfPage(page: Page, owner: Map<string, SchemaPluginInfo>): SchemaPluginInfo | undefined {
  for (const own of page.owns) {
    const plugin = owner.get(group(own));
    if (plugin && group(own) === own) return plugin;
  }
  return undefined;
}
