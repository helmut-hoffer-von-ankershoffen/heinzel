import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { acmeTheme, everyday, shell, theme, themeList } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";

type Desktop = Window & { heinzelDesktop?: unknown };
const root = document.documentElement;
const variable = (name: string) => root.style.getPropertyValue(name);

function desktop() {
  let relay: ((full: boolean) => void) | null = null;
  (window as Desktop).heinzelDesktop = Object.freeze({
    titlebar: Object.freeze({ height: 44, left: 80, right: 0 }),
    onFullscreen: (callback: (full: boolean) => void) => {
      relay = callback;
      return () => {
        relay = null;
      };
    },
  });
  return (full: boolean) => act(() => relay?.(full));
}

function brandedTheme(name: string, title: string) {
  return theme({
    name,
    title,
    logo: {
      alt: title,
      light: `/theme-assets/${name}/light.svg`,
      dark: `/theme-assets/${name}/dark.svg`,
      mark: `/theme-assets/${name}/mark.svg`,
      height: "24px",
    },
  });
}

function api(selected = "default") {
  mockApi({
    ...shell,
    "/api/system/expert": everyday,
    "/api/system/theme/list": themeList(selected, [
      theme(),
      acmeTheme,
      brandedTheme("heinzelmaennchen", "Heinzelmännchen"),
      brandedTheme("soundflake", "Soundflake"),
    ]),
  });
}

afterEach(() => {
  delete (window as Desktop).heinzelDesktop;
  delete root.dataset.desktopTitlebar;
});

describe("Integrated title bar", () => {
  it("leaves a browser's layout as it is: no title bar mode and every inset 0", async () => {
    api();
    renderAt("/doctor");
    const navigation = screen.getByRole("complementary", { name: "Main" });
    await within(navigation).findByRole("link", { name: "Today" });
    expect(root).not.toHaveAttribute("data-desktop-titlebar");
    expect(variable("--titlebar-height")).toBe("0px");
    expect(variable("--titlebar-inset-left")).toBe("0px");
    expect(navigation.querySelector("[data-titlebar]")).toBeNull();
  });

  it.each(["default", "heinzelmaennchen"])(
    "retains the native controls and omits the %s brand row",
    async (selected) => {
      const fullscreen = desktop();
      api(selected);
      const user = userEvent.setup();
      renderAt("/doctor");
      const navigation = screen.getByRole("complementary", { name: "Main" });
      await within(navigation).findByRole("link", { name: "Heinzelmännchen" });
      if (selected === "heinzelmaennchen") await waitFor(() => expect(variable("--radius-lg")).toBe("0.5rem"));
      expect(root).toHaveAttribute("data-desktop-titlebar");
      expect(variable("--titlebar-height")).toBe("44px");
      expect(variable("--titlebar-inset-left")).toBe("80px");
      expect(variable("--titlebar-main-start")).toBe("0px");
      const row = navigation.querySelector<HTMLElement>("[data-titlebar]");
      expect(row).not.toBeNull();
      const controls = Array.from((row as HTMLElement).querySelectorAll("a, button"), (element) =>
        element.getAttribute("aria-label"),
      );
      expect(controls).toEqual(["Collapse navigation", "Today", "Notifications", "Search"]);
      expect(within(navigation).queryByTestId("heinzel-wordmark")).toBeNull();
      expect(row?.nextElementSibling).toBe(navigation.querySelector("nav"));
      await expectAccessible(navigation);
      // Full screen hides the lights: their inset goes back to the row.
      fullscreen(true);
      expect(variable("--titlebar-inset-left")).toBe("0px");
      fullscreen(false);
      expect(variable("--titlebar-inset-left")).toBe("80px");
      // Collapsed: the expand button stays in the row after the lights; the main header starts after it.
      await user.click(within(row as HTMLElement).getByRole("button", { name: "Collapse navigation" }));
      const railRow = navigation.querySelector<HTMLElement>("[data-titlebar]");
      expect(within(railRow as HTMLElement).getByRole("button", { name: "Expand navigation" })).toBeInTheDocument();
      expect(variable("--titlebar-main-start")).toBe("64px");
      expect(within(navigation).getByRole("link", { name: "Today" })).toBeInTheDocument();
    },
  );

  it.each([
    ["acme", "Acme"],
    ["soundflake", "Soundflake"],
  ])("retains the %s home link below the native title bar", async (selected, alt) => {
    desktop();
    api(selected);
    renderAt("/doctor");
    const navigation = screen.getByRole("complementary", { name: "Main" });
    const logo = await within(navigation).findByRole("img", { name: alt });
    const brand = logo.closest("a");
    expect(brand).toHaveAccessibleName("Heinzelmännchen home");
    expect(brand).toHaveAttribute("href", "/");
    expect(navigation.querySelector("[data-titlebar]")?.nextElementSibling).toContainElement(brand);
  });
});
