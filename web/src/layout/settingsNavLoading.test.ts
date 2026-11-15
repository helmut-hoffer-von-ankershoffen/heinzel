// Where plugin settings pages go, for every mix of plugins (connectors, layers, preferences, declared or not), both
// while the installed plugins still load and once they have: never in a wrong place first and moved later.
import { describe, expect, it } from "vitest";
import type { SchemaNavEntry, SchemaPluginInfo } from "@/api/client";
import { loadedPlugins, settingsNav } from "./settingsSections";

const plugin = (overrides: Partial<SchemaPluginInfo>): SchemaPluginInfo =>
  ({
    distribution: `heinzel-${overrides.name}`,
    version: "0.3.0",
    modules: [overrides.name ?? ""],
    summary: "",
    source_kind: "git",
    source_url: null,
    revision: null,
    homepage: null,
    first_party: true,
    enabled: true,
    error: null,
    layers: [],
    capabilities: [],
    playbooks: [],
    themes: [],
    kinds: [],
    ...overrides,
  }) as SchemaPluginInfo;

const layer = (name: string, label: "company" | "org" | "site" | "team", parent: string | null = null) => ({
  layers: [{ module: name, parent, site: null, label }],
});

/** A plugin's own settings page as the server lists it: undeclared unless said, under Preferences by default. */
const page = (name: string, extra: Partial<SchemaNavEntry> = {}): SchemaNavEntry => ({
  id: `${name}:@settings`,
  module: name,
  plugin: `heinzel-${name}`,
  title: name,
  icon: "code",
  declared: false,
  order: 1000,
  settings: { group: "preferences", sections: [name] },
  sidebar: null,
  ...extra,
});

const google = plugin({ name: "google", capabilities: ["google:mail", "google:calendar"], kinds: ["connector"] });
const atlassian = plugin({ name: "atlassian", capabilities: ["atlassian:tracker"], kinds: ["connector"] });
const connectors = plugin({
  name: "acme-connectors",
  summary: "ACME's connectors for every capability.",
  capabilities: ["acme-connectors:mail", "acme-connectors:chat"],
});
const acme = plugin({ name: "acme", ...layer("acme", "company") });
const lisbon = plugin({ name: "acme-lisbon", ...layer("acme-lisbon", "site", "acme") });
const layeredConnector = plugin({
  name: "acme-tools",
  capabilities: ["acme-tools:tracker"],
  ...layer("acme-tools", "team", "acme"),
});
const weather = plugin({ name: "weather", capabilities: ["weather:weather"] });
const notes = plugin({ name: "notes" });

const ACME_MIX = [google, atlassian, connectors, acme, lisbon];
const where = (nav: ReturnType<typeof settingsNav>, name: string) =>
  nav.pages.find((entry) => entry.id === `${name}:@settings`)?.heading;

describe("Plugin settings pages while the installed plugins load", () => {
  it.each([
    ["connector plugins only", [google, atlassian]],
    ["the ACME layers and connectors", ACME_MIX],
    ["a connector that is also a layer", [acme, layeredConnector]],
    ["preference plugins", [weather, notes]],
  ])("places no undeclared plugin page and lists no connector yet: %s", (_mix, plugins) => {
    const names = plugins.map((item) => item.name);
    const nav = settingsNav(
      names,
      undefined,
      names.map((name) => page(name)),
    );
    expect(nav.loading).toBe(true);
    expect(nav.connectors).toEqual([]);
    for (const name of names) expect(where(nav, name)).toBeUndefined();
  });

  it("places a declared page at once, where the server declares it", () => {
    const nav = settingsNav(["notes"], undefined, [
      page("notes", { declared: true, settings: { group: "customize", sections: ["notes"] } }),
    ]);
    expect(where(nav, "notes")).toBe("customize");
  });
});

describe("Plugin settings pages once the installed plugins are known", () => {
  it("keeps connectors on Connectors with the ACME layers installed, and layers under Organisation", () => {
    const names = ACME_MIX.map((item) => item.name);
    const nav = settingsNav(
      names,
      ACME_MIX,
      names.map((name) => page(name)),
    );
    expect(nav.loading).toBe(false);
    expect(nav.connectors.map((entry) => entry.title)).toEqual(["ACME Connectors", "Atlassian", "Google"]);
    for (const name of ["google", "atlassian", "acme-connectors"]) expect(where(nav, name)).toBeUndefined();
    expect(where(nav, "acme")).toBe("organisation");
    expect(where(nav, "acme-lisbon")).toBe("organisation");
  });

  it("keeps connectors on Connectors without any layer", () => {
    const nav = settingsNav(["google", "atlassian"], [google, atlassian], [page("google"), page("atlassian")]);
    expect(nav.connectors.map((entry) => entry.id)).toEqual(["atlassian", "google"]);
    expect(nav.pages.filter((entry) => entry.heading === "preferences").map((entry) => entry.id)).toEqual([
      "appearance",
      "notifications",
    ]);
  });

  it("gives a connector that is also a layer both its layer page and its connector card", () => {
    const nav = settingsNav(["acme-tools"], [acme, layeredConnector], [page("acme-tools")]);
    expect(where(nav, "acme-tools")).toBe("organisation");
    expect(nav.connectors.map((entry) => entry.id)).toEqual(["acme-tools"]);
  });

  it("puts a preference plugin under Preferences, with or without capabilities", () => {
    const nav = settingsNav(["weather", "notes"], [weather, notes], [page("weather"), page("notes")]);
    expect(where(nav, "notes")).toBe("preferences");
    expect(nav.connectors.map((entry) => entry.id)).toEqual(["weather"]);
  });

  it("still gives a page whose plugin isn't installed a Preferences page", () => {
    const nav = settingsNav(["ghost"], [], [page("ghost")]);
    expect(where(nav, "ghost")).toBe("preferences");
  });
});

describe("The plugin list's state", () => {
  it("is loading only while the query is pending; a failed list counts as no plugins, never as loading", () => {
    expect(loadedPlugins({ isPending: true, data: undefined })).toBeUndefined();
    expect(loadedPlugins({ isPending: false, data: [google] })).toEqual([google]);
    const failed = loadedPlugins({ isPending: false, data: undefined });
    expect(failed).toEqual([]);
    const nav = settingsNav(["google"], failed, [page("google")]);
    expect(nav.loading).toBe(false);
    expect(where(nav, "google")).toBe("preferences");
    expect(nav.connectors).toEqual([]);
  });
});
