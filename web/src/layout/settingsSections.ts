// Where each part of Settings lives, as in a desktop chat app: Preferences for hz and every module with settings,
// built-in or from a plugin; Customize for Connectors (plugins that connect your tools: they bring adapters, and their
// settings live on their cards) and Plugins (installing and managing them); This computer for the configuration files.
import type { SchemaPluginInfo } from "@/api/client";

export const FILES = "files";
export const PLUGINS = "plugins";
export const CONNECTORS = "connectors";
/** The settings of the clock plugin: its time zones and working hours, which the status bar's clock links to. */
export const CLOCK_SETTINGS = "clock";
/** The `[capabilities]` table: which adapter serves which capability; shown on the Connectors page. */
export const BINDINGS = "capabilities";

/** `system.runner` → `system`: the left side lists modules; their sub-sections are headings on the right. */
export const group = (section: string) => section.split(".")[0] ?? section;

const BRANDS: Record<string, string> = { github: "GitHub", gitlab: "GitLab", hz: "hz" };
/** Words written in capitals wherever they appear in a name: `ai access` → `AI access`, never `Ai access`. */
const ACRONYMS: Record<string, string> = {
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

/** A plugin's name as its authors write it: the casing its summary uses (`GitHub`), else a known brand, else `Clock`. */
export function displayName(name: string, summary = ""): string {
  const words = name.replaceAll("-", " ").replaceAll("_", " ");
  const escaped = words.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const found = summary.match(new RegExp(`\\b${escaped}\\b`, "i"))?.[0];
  if (found && found !== found.toLowerCase()) return found;
  return BRANDS[name.toLowerCase()] ?? inWords(words);
}

/** A plugin's summary without the "…: a Heinzelmännchen plugin." its template ends it with; the header says so. */
export const plainSummary = (summary: string) =>
  summary.replace(/\s*[:;,–—-]\s*an? Heinzelmännchen plugin\.?\s*$/i, ".");

export type SettingsEntry = { id: string; title: string; plugin?: SchemaPluginInfo };

export type SettingsNav = {
  /** hz itself ("General") first, then every other module with settings, built-in or from a plugin, by name. */
  preferences: SettingsEntry[];
  /** Plugins that bring adapters, each with the setting groups of its modules. */
  connectors: (SettingsEntry & { plugin: SchemaPluginInfo; groups: string[] })[];
};

/** Sorts the setting groups into Preferences and Connectors by the plugin that provides each module. */
export function settingsNav(groups: string[], plugins: SchemaPluginInfo[]): SettingsNav {
  const owner = new Map<string, SchemaPluginInfo>();
  for (const plugin of plugins) for (const module of plugin.modules) owner.set(group(module), plugin);
  const connects = (plugin: SchemaPluginInfo | undefined) => (plugin?.capabilities ?? []).length > 0;
  const of = (plugin: SchemaPluginInfo) => groups.filter((name) => owner.get(name) === plugin);
  const byTitle = (a: SettingsEntry, b: SettingsEntry) => a.title.localeCompare(b.title);
  const preferences = groups
    .filter((name) => name !== BINDINGS && !connects(owner.get(name)))
    .map((name) => {
      const plugin = owner.get(name);
      return { id: name, title: name === "hz" ? "" : displayName(name, plugin?.summary), plugin };
    })
    .sort((a, b) => (a.id === "hz" ? -1 : b.id === "hz" ? 1 : byTitle(a, b)));
  return {
    preferences,
    connectors: plugins
      .filter(connects)
      .map((plugin) => ({
        id: plugin.name,
        title: displayName(plugin.name, plugin.summary),
        plugin,
        groups: of(plugin),
      }))
      .sort(byTitle),
  };
}
