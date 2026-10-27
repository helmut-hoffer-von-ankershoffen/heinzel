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
/** The settings of the clock plugin: its time zones and working hours, which the status bar's clock links to. */
export const CLOCK_SETTINGS = "clock";
/** The `[capabilities]` table: which adapter serves which capability; shown on the Connectors page. */
export const BINDINGS = "capabilities";

/** `system.runner` → `system`: the left side lists modules; their sub-sections are headings on the right. */
export const group = (section: string) => section.split(".")[0] ?? section;

const BRANDS: Record<string, string> = { acme: "ACME", github: "GitHub", gitlab: "GitLab", hz: "hz" };
/** Words written in capitals wherever they appear in a name: `ai access` → `AI access`, never `Ai access`. */
const ACRONYMS: Record<string, string> = {
  ai: "AI",
  api: "API",
  acme: "ACME",
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

/** A plugin's name as its authors write it: the casing its summary uses (`GitHub`), else `Clock`; a known brand (`World Clock`) first. */
export function displayName(name: string, summary = ""): string {
  const words = name.replaceAll("-", " ").replaceAll("_", " ");
  const escaped = words.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const found = summary.match(new RegExp(`\\b${escaped}\\b`, "i"))?.[0];
  if (name.toLowerCase() in BRANDS) return BRANDS[name.toLowerCase()] as string;
  if (found && found !== found.toLowerCase()) return found;
  return inWords(words);
}

/** A plugin's summary without the "…: a Heinzelmännchen plugin." its template ends it with; the header says so. */
export const plainSummary = (summary: string) =>
  summary.replace(/\s*[:;,–—-]\s*an? Heinzelmännchen plugin\.?\s*$/i, ".");

export type SettingsEntry = { id: string; title: string; plugin?: SchemaPluginInfo };

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
  { id: CONNECTORS, title: "Connectors", icon: Plug, heading: "customize", owns: [] },
  { id: PLUGINS, title: "Plugins", icon: Puzzle, heading: "customize", owns: [] },
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
  { id: FROM_CLAUDE_PAGE, title: "From Claude", icon: Download, heading: "computer", owns: [] },
  {
    id: "updates",
    title: "Updates",
    icon: RefreshCw,
    heading: "computer",
    owns: ["system.changelog", "system.plugin"],
    keywords: "release notes github token",
  },
  { id: "system", title: "System", icon: Cpu, heading: "computer", owns: ["system"], keywords: "directory" },
];

/** Old links by what they meant: `?settings=hz` was General, whose appearance and AI access now live elsewhere. */
const ALIASES: Record<string, string> = { hz: "appearance", org: "you", heinzel: "team", runner: "ai" };

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

export type SettingsNav = {
  /** Every page, by heading order: the fixed ones, then a page for each plugin module hz doesn't know. */
  pages: Page[];
  /** Plugins that bring adapters, each with the setting groups of its modules. */
  connectors: (SettingsEntry & { plugin: SchemaPluginInfo; groups: string[] })[];
};

/** A section id from a link (`?settings=`), as the page it opens: a page's id, a section it owns, or an old name. */
export function resolvePage(id: string | null | undefined, pages: Page[]): string | undefined {
  if (!id) return undefined;
  if (pages.some((page) => page.id === id)) return id;
  const alias = ALIASES[id];
  if (alias && pages.some((page) => page.id === alias)) return alias;
  return pageOf(id, pages)?.id;
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
  const extra: Page[] = navigation.flatMap((entry) => {
    if (!entry.settings || entry.problem) return [];
    const plugin = plugins.find((item) => item.distribution === entry.plugin || item.name === entry.plugin);
    if (plugin && (!plugin.enabled || plugin.error)) return [];
    // Legacy provider metadata must not move host settings out of their existing conceptual pages.
    if (
      !entry.declared &&
      ((connects(plugin) && !preferenceIcon(plugin)) ||
        entry.settings.sections.every((section) => PAGES.some((page) => owns(page, section))))
    )
      return [];
    return [
      {
        id: entry.id,
        title: entry.module === "clock" ? "World clock" : displayName(entry.title, entry.title),
        icon: !entry.declared && entry.icon === "code" ? (preferenceIcon(plugin) ?? ICONS.code) : ICONS[entry.icon],
        heading: entry.settings.group,
        owns: entry.settings.sections,
        plugin,
        module: entry.module,
        provider: entry.plugin,
      },
    ];
  });
  const pages = [...PAGES, ...extra].map((page) => ({ ...page, plugin: page.plugin ?? pluginOfPage(page, owner) }));
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

function pluginOfPage(page: Page, owner: Map<string, SchemaPluginInfo>): SchemaPluginInfo | undefined {
  for (const own of page.owns) {
    const plugin = owner.get(group(own));
    if (plugin && group(own) === own) return plugin;
  }
  return undefined;
}
