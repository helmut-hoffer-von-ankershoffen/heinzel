import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Palette } from "lucide-react";
import { describe, expect, it } from "vitest";
import type { SchemaNavEntry, SchemaPluginInfo } from "@/api/client";
import { ICONS } from "@/components/Contributions";
import { config, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { sourceLabel } from "./PluginsSection";
import { pageOf, settingsNav } from "./settingsSections";

const provider: SchemaPluginInfo = {
  name: "harbor",
  distribution: "heinzel-harbor",
  version: "1.0.0",
  modules: ["harbor"],
  summary: "Local conditions.",
  first_party: false,
  enabled: true,
  error: null,
  source_kind: "git",
  source_url: null,
  revision: null,
  homepage: null,
  layers: [],
  capabilities: [],
  playbooks: [],
  themes: [],
  kinds: [],
};
const entry: SchemaNavEntry = {
  id: "harbor:conditions",
  module: "harbor",
  plugin: "heinzel-harbor",
  title: "Local conditions",
  icon: "cloud-sun",
  declared: true,
  order: 50,
  settings: { group: "preferences", sections: ["harbor"] },
  sidebar: { kind: "navigate", path: "/doctor?panel=conditions" },
};
const answers = {
  ...shell,
  "/api/system/plugin/list": [provider],
  "/api/system/ui/navigation": [entry],
  "/api/system/config/show": {
    ...config,
    settings: [
      ...config.settings,
      {
        section: "harbor",
        key: "place",
        value: "Lisbon",
        default: "Lisbon",
        source: "default",
        env: "HZ_HARBOR__PLACE",
        description: "The location to display.",
        section_title: "Conditions service",
        json_schema: { type: "string", title: "Weather location" },
      },
    ],
  },
};

describe("navigation declared by any installed provider", () => {
  it("preserves provider titles and gives legacy theme/weather preferences semantic closed icons", () => {
    const theme = {
      ...provider,
      name: "acme",
      distribution: "heinzel-acme",
      modules: ["acme"],
      kinds: ["theme" as const],
    };
    const weather = { ...provider, capabilities: ["field-notes:weather"] };
    const legacy = (module: string): SchemaNavEntry => ({
      ...entry,
      id: `${module}:@settings`,
      module,
      plugin: `heinzel-${module}`,
      title: module === "harbor" ? "Weather" : "ACME",
      icon: "code",
      declared: false,
      settings: { sections: [module], group: "preferences" },
    });
    const nav = settingsNav(["acme", "harbor"], [theme, weather], [legacy("acme"), legacy("harbor")]);
    expect(nav.pages.find((page) => page.id === "acme:@settings")).toMatchObject({ title: "ACME", icon: Palette });
    expect(nav.pages.find((page) => page.id === "harbor:@settings")).toMatchObject({
      title: "Weather",
      icon: ICONS["cloud-sun"],
    });
    const declared = settingsNav(["harbor"], [weather], [{ ...entry, title: "weather", icon: "code" }]);
    expect(declared.pages.find((page) => page.id === entry.id)).toMatchObject({ title: "weather", icon: ICONS.code });
    const mixedCase = settingsNav(["harbor"], [weather], [{ ...entry, title: "eBay settings" }]);
    expect(mixedCase.pages.find((page) => page.id === entry.id)?.title).toBe("eBay settings");
  });

  it("renders ACME and Weather preference labels with semantic icons in the actual Settings navigation", async () => {
    const theme = {
      ...provider,
      name: "acme",
      distribution: "heinzel-acme",
      modules: ["acme"],
      kinds: ["theme" as const],
    };
    const weather = {
      ...provider,
      name: "weather",
      distribution: "heinzel-weather",
      modules: ["weather"],
      capabilities: ["openmeteo:weather"],
    };
    const navigation = ["acme", "weather"].map(
      (name): SchemaNavEntry => ({
        ...entry,
        id: `${name}:@settings`,
        module: name,
        plugin: `heinzel-${name}`,
        title: name === "acme" ? "ACME" : "Weather",
        icon: "code",
        declared: false,
        settings: { group: "preferences", sections: [name] },
      }),
    );
    mockApi({
      ...answers,
      "/api/system/plugin/list": [theme, weather],
      "/api/system/ui/navigation": navigation,
      "/api/system/config/show": { ...config, settings: [] },
    });
    renderAt("/doctor?settings=acme:@settings");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    const nav = await within(dialog).findByRole("navigation", { name: "Settings" });
    const acme = await within(nav).findByRole("button", { name: "ACME" });
    const forecast = within(nav).getByRole("button", { name: "Weather" });
    expect(acme.querySelector(".lucide-palette")).toBeInTheDocument();
    expect(forecast.querySelector(".lucide-cloud-sun")).toBeInTheDocument();
    expect(acme.querySelector(".lucide-code")).not.toBeInTheDocument();
    await userEvent.setup().click(forecast);
    expect(await within(dialog).findByRole("heading", { name: "Weather" })).toBeInTheDocument();
  });

  it("offers the same provider and every host page through the compact page selector", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor?settings=appearance");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    const picker = await within(dialog).findByRole("combobox", { name: "Settings page" });
    expect(within(picker).getByRole("option", { name: "Local conditions" })).toBeInTheDocument();
    expect(within(picker).getByRole("option", { name: "Configuration files" })).toBeInTheDocument();
    await user.selectOptions(picker, entry.id);
    expect(await within(dialog).findByLabelText("Weather location")).toHaveValue("Lisbon");
    expect(picker).toHaveValue(entry.id);
    await user.type(within(dialog).getByRole("searchbox"), "location");
    expect(await within(dialog).findByRole("heading", { name: "Matching settings" })).toBeInTheDocument();
    await user.selectOptions(picker, "appearance");
    expect(within(dialog).getByRole("searchbox")).toHaveValue("");
    expect(await within(dialog).findByRole("radiogroup", { name: "Mode" })).toBeInTheDocument();
  });

  it("uses declared title/icon in settings and the same declared route in full and collapsed navigation", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    const view = renderAt("/doctor?settings=harbor");
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    const settingsNav = await within(dialog).findByRole("navigation", { name: "Settings" });
    const settingsEntry = await within(settingsNav).findByRole("button", { name: "Local conditions" });
    expect(settingsEntry.querySelector("svg")).toHaveClass("lucide-cloud-sun");
    expect(await within(dialog).findByLabelText("Weather location")).toHaveValue("Lisbon");
    expect(within(dialog).getByRole("region", { name: "Conditions service" })).toBeInTheDocument();
    expect(within(dialog).queryByText("Private", { exact: true })).not.toBeInTheDocument();
    expect(within(dialog).getAllByText("source not reported yet", { exact: true }).length).toBeGreaterThan(0);
    await user.click(within(dialog).getByRole("button", { name: "Close settings" }));
    const link = screen.getByRole("link", { name: "Local conditions" });
    expect(link).toHaveAttribute("href", "/doctor?panel=conditions");
    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(screen.getByRole("link", { name: "Local conditions" })).toHaveAttribute("href", "/doctor?panel=conditions");
    await expectAccessible(view.container);
  });

  it.each(["not installed", "disabled"])(
    "omits absent contributions when a provider is %s, even if old config remains",
    async (state) => {
      mockApi({
        ...answers,
        "/api/system/ui/navigation": [],
        "/api/system/plugin/list": state === "disabled" ? [{ ...provider, enabled: false }] : [],
      });
      renderAt("/doctor?settings=appearance");
      const dialog = await screen.findByRole("dialog", { name: "Settings" });
      const nav = await within(dialog).findByRole("navigation", { name: "Settings" });
      expect(within(nav).queryByRole("button", { name: "World Clock" })).not.toBeInTheDocument();
      expect(within(nav).queryByRole("button", { name: "Local conditions" })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Local conditions" })).not.toBeInTheDocument();
    },
  );

  it("keeps legacy host sections on their conceptual pages and never invents private provenance", () => {
    const fallback: SchemaNavEntry = {
      ...entry,
      id: "system.daemon:@settings",
      module: "system.daemon",
      plugin: null,
      title: "Internal daemon",
      declared: false,
      settings: { group: "preferences", sections: ["system.daemon"] },
      sidebar: null,
    };
    const nav = settingsNav(["system", "harbor"], [provider], [fallback, entry]);
    expect(pageOf("system.daemon", nav.pages)?.id).toBe("daemon");
    expect(nav.pages.some((page) => page.id === fallback.id)).toBe(false);
    expect(pageOf("harbor", nav.pages)?.id).toBe(entry.id);
    expect(sourceLabel(provider)).toBe("source not reported yet");
    expect(sourceLabel(provider, false, "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-contrib")).toBe(
      "Community",
    );
    expect(sourceLabel(provider, false, "https://github.com/acme-example/heinzel-harbor")).toBe("Custom");
    expect(sourceLabel(provider, true, "https://github.com/acme-example/heinzel-harbor")).toBe("Private");
    expect(sourceLabel(provider, false, "https://github.com/acme-example/heinzel-harbor")).toBe("Custom");
  });
});
