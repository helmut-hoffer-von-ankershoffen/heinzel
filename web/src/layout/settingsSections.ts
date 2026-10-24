// Where each part of Settings lives, as in a desktop chat app: Preferences for hz and its built-in modules; Customize
// for Connectors (plugins that connect your tools: they bring adapters) and Plugins, with every other plugin's
// settings as an entry of its own under Plugins; This computer for the configuration files.
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

/** A plugin's name as its authors write it: the casing its summary uses (`GitHub`), else a known brand, else `Clock`. */
export function displayName(name: string, summary = ""): string {
  const words = name.replaceAll("-", " ").replaceAll("_", " ");
  const escaped = words.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const found = summary.match(new RegExp(`\\b${escaped}\\b`, "i"))?.[0];
  if (found && found !== found.toLowerCase()) return found;
  return BRANDS[name.toLowerCase()] ?? words.charAt(0).toUpperCase() + words.slice(1);
}

export type SettingsEntry = { id: string; title: string; plugin?: SchemaPluginInfo };

export type SettingsNav = {
  /** hz itself ("General") and the built-in modules. */
  preferences: SettingsEntry[];
  /** Plugins that bring adapters, each with the setting groups of its modules. */
  connectors: (SettingsEntry & { plugin: SchemaPluginInfo; groups: string[] })[];
  /** Every other plugin with settings, listed under Plugins. */
  plugins: (SettingsEntry & { groups: string[] })[];
};

/** Sorts the setting groups into Preferences, Connectors and Plugins by the plugin that provides each module. */
export function settingsNav(groups: string[], plugins: SchemaPluginInfo[]): SettingsNav {
  const owner = new Map<string, SchemaPluginInfo>();
  for (const plugin of plugins) for (const module of plugin.modules) owner.set(group(module), plugin);
  const builtIn = groups
    .filter((name) => !owner.has(name) && name !== BINDINGS)
    .sort((a, b) => (a === "hz" ? -1 : b === "hz" ? 1 : a.localeCompare(b)));
  const of = (plugin: SchemaPluginInfo) => groups.filter((name) => owner.get(name) === plugin);
  const title = (plugin: SchemaPluginInfo) => displayName(plugin.name, plugin.summary);
  const byTitle = (a: SettingsEntry, b: SettingsEntry) => a.title.localeCompare(b.title);
  return {
    preferences: builtIn.map((name) => ({ id: name, title: name === "hz" ? "" : displayName(name) })),
    connectors: plugins
      .filter((plugin) => (plugin.capabilities ?? []).length > 0)
      .map((plugin) => ({ id: plugin.name, title: title(plugin), plugin, groups: of(plugin) }))
      .sort(byTitle),
    plugins: plugins
      .filter((plugin) => (plugin.capabilities ?? []).length === 0 && of(plugin).length > 0)
      .map((plugin) => ({ id: plugin.name, title: title(plugin), plugin, groups: of(plugin) }))
      .sort(byTitle),
  };
}
