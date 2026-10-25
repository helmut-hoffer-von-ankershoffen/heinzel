import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { acmeTheme, config, shell, theme, themeList } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { previewStyle } from "./ThemePreviews";

const root = () => document.documentElement.style;

afterEach(() => {
  for (const name of ["--radius-lg", "--radius-xl", "--color-icon", "--color-main-tint"]) root().removeProperty(name);
  localStorage.clear();
});

/** The API with a theme list that follows `hz system theme use`. */
function themedApi(initial: string) {
  let selected = initial;
  return mockApi({
    ...shell,
    get "/api/system/theme/list"() {
      return themeList(selected);
    },
    get "/api/system/theme/use"() {
      selected = "acme";
      return { selected };
    },
  });
}

describe("Themes", () => {
  it("uses the custom Appearance controls once and keeps hidden/expert raw fields out of the page", async () => {
    const managed = [
      ["system.theme", "name", "default"],
      ["system.sound", "name", "off"],
      ["system.sound", "volume", 50],
      ["system.sound", "follow_theme", false],
      ["system.sound", "mute_in_meetings", true],
      ["system.sound", "duck_while_typing", true],
    ] as const;
    mockApi({
      ...shell,
      "/api/system/config/show": {
        ...config,
        settings: [
          ...config.settings,
          ...managed.map(([section, key, value]) => ({
            section,
            key,
            value,
            default: value,
            source: "default",
            env: "EXAMPLE",
            description: "Managed by custom Appearance controls.",
            json_schema: { "x-hidden": true },
          })),
          {
            section: "system.theme",
            key: "diagnostic",
            value: true,
            default: false,
            source: "default",
            env: "EXAMPLE",
            description: "Expert theme diagnostic.",
            json_schema: { type: "boolean", "x-expert": true },
          },
        ],
      },
      "/api/system/theme/list": themeList("default", [theme()]),
      "/api/system/sound/list": {
        selected: "off",
        effective: "off",
        volume: 50,
        follow_theme: false,
        mute_in_meetings: true,
        duck_while_typing: true,
        signals: {},
        soundscapes: [
          {
            name: "off",
            title: "Off",
            kind: "off",
            module: null,
            plugin: null,
            selected: true,
            effective: true,
            fade_seconds: 4,
          },
        ],
      },
    });
    const user = userEvent.setup();
    const { container } = renderAt("/doctor?settings=appearance");
    expect(await screen.findByRole("slider", { name: "Volume" })).toBeInTheDocument();
    expect(screen.getAllByRole("slider", { name: "Volume" })).toHaveLength(1);
    expect(screen.getAllByRole("switch", { name: "Follow my theme" })).toHaveLength(1);
    expect(screen.queryByText("system.sound")).not.toBeInTheDocument();
    expect(screen.queryByText("system.theme")).not.toBeInTheDocument();
    expect(screen.queryByText("Expert theme diagnostic.")).not.toBeInTheDocument();
    expect(screen.queryByText("No setting matches.")).not.toBeInTheDocument();
    await user.type(screen.getByRole("searchbox", { name: "Filter settings" }), "volume");
    expect(screen.getByRole("region", { name: "Settings pages" })).toHaveTextContent("Appearance");
    expect(screen.queryByText("No setting matches.")).not.toBeInTheDocument();
    await expectAccessible(container);
  });
  it("switching to a theme with other corners and icon colour changes the app's custom properties", async () => {
    const { calls } = themedApi("default");
    const user = userEvent.setup();
    const { container } = renderAt("/doctor?settings=hz");
    const group = await screen.findByRole("radiogroup", { name: "Theme" });
    const acme = await within(group).findByRole("radio", { name: "Acme" });
    expect(within(group).getByRole("radio", { name: "Default" })).toHaveAttribute("aria-checked", "true");
    expect(root().getPropertyValue("--radius-lg")).toBe(""); // the default theme is index.css itself
    await user.click(acme);
    await waitFor(() => expect(root().getPropertyValue("--radius-lg")).toBe("0rem"));
    expect(root().getPropertyValue("--radius-xl")).toBe("0.25rem");
    expect(root().getPropertyValue("--color-icon")).toBe("#0f766e");
    expect(root().getPropertyValue("--color-main-tint")).toBe("#f7f4ee");
    expect(acme).toHaveAttribute("aria-checked", "true");
    const use = calls.find((call) => call.url.includes("/system/theme/use"));
    expect(new URL(use?.url ?? "").searchParams.get("name")).toBe("acme");
    await expectAccessible(container);
  });

  it("draws each preview with its own theme's tokens, corners derived from its radii", async () => {
    themedApi("default");
    renderAt("/doctor?settings=hz");
    const preview = await screen.findByTestId("theme-preview-acme");
    expect(preview.style.getPropertyValue("--radius-lg")).toBe("0rem");
    expect(preview.style.getPropertyValue("--color-icon")).toBe("#0f766e");
    expect(preview.style.getPropertyValue("--radius-md")).toBe("calc(var(--radius-lg) * 0.75)");
    expect(screen.getByTestId("theme-preview-default").style.getPropertyValue("--color-main-tint")).toBe("transparent");
  });

  it("brands each preview as the sidebar would: the theme's wordmark, else the Heinzel and the name", async () => {
    themedApi("default");
    renderAt("/doctor?settings=hz");
    const acme = await screen.findByTestId("theme-preview-acme");
    expect(acme.querySelector("img")).toHaveAttribute("src", "/theme-assets/acme/light.svg?v=1");
    const builtIn = screen.getByTestId("theme-preview-default");
    expect(builtIn.querySelector("img")).toBeNull();
    expect(within(builtIn).getByTestId("heinzel-icon")).toBeInTheDocument();
    expect(builtIn).toHaveTextContent("Heinzelmännchen");
  });

  it("names the plugin under a preview only when it says more than the title", async () => {
    const night = { ...acmeTheme, name: "acme-night", title: "Acme Night" };
    mockApi({ ...shell, "/api/system/theme/list": themeList("default", [theme(), acmeTheme, night]) });
    renderAt("/doctor?settings=hz");
    const group = await screen.findByRole("radiogroup", { name: "Theme" });
    expect(await within(group).findByRole("radio", { name: "Acme" })).not.toHaveTextContent(/acme$/);
    const second = within(group).getByRole("radio", { name: "Acme Night · acme" });
    expect(within(second).getByText("acme")).toBeInTheDocument();
  });

  it("never turns a theme's values into CSS text: only custom properties and the font stack", () => {
    const style = previewStyle(acmeTheme, false) as Record<string, unknown>;
    expect(Object.keys(style).every((key) => key.startsWith("--") || key === "fontFamily")).toBe(true);
  });

  it("shows the theme's logo in the sidebar, and its mark in the collapsed rail", async () => {
    themedApi("acme");
    const user = userEvent.setup();
    renderAt("/doctor");
    const navigation = screen.getByRole("complementary", { name: "Main" });
    const logo = await within(navigation).findByRole("img", { name: "Acme" });
    expect(logo).toHaveAttribute("src", "/theme-assets/acme/light.svg?v=1");
    expect(logo.style.height).toBe("28px");
    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(await within(navigation).findByRole("img", { name: "Acme" })).toHaveAttribute(
      "src",
      "/theme-assets/acme/mark.svg?v=1",
    );
  });

  it("shows the Heinzel and the app's name for a theme without a logo, the built-in one included", async () => {
    const logo = { light: "/theme-assets/default/light.svg", dark: "/x.svg", mark: null, alt: "x", height: "24px" };
    mockApi({ ...shell, "/api/system/theme/list": themeList("default", [theme({ logo }), acmeTheme]) });
    const user = userEvent.setup();
    renderAt("/doctor");
    const navigation = screen.getByRole("complementary", { name: "Main" });
    expect(await within(navigation).findByText("Heinzelmännchen")).toBeInTheDocument();
    expect(within(navigation).getByTestId("heinzel-icon").querySelector("svg")).toBeInTheDocument();
    expect(within(navigation).queryByRole("img")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(within(navigation).getByTestId("heinzel-icon")).toBeInTheDocument();
  });
});
