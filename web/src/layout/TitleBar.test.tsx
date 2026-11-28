import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { everyday, shell, themeList } from "@/test/fixtures";
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

function api(selected = "default") {
  mockApi({
    ...shell,
    "/api/system/expert": everyday,
    "/api/system/theme/list": themeList(selected),
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

  it("puts the toggle, Home, Bell and Search after the lights and the built-in navigation directly below", async () => {
    const fullscreen = desktop();
    api();
    const user = userEvent.setup();
    renderAt("/doctor");
    const navigation = screen.getByRole("complementary", { name: "Main" });
    await within(navigation).findByRole("link", { name: "Heinzelmännchen" });
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
  });

  it("retains a selected plugin theme's home link below the native title bar", async () => {
    desktop();
    api("acme");
    renderAt("/doctor");
    const navigation = screen.getByRole("complementary", { name: "Main" });
    const logo = await within(navigation).findByRole("img", { name: "Acme" });
    const brand = logo.closest("a");
    expect(brand).toHaveAccessibleName("Heinzelmännchen home");
    expect(brand).toHaveAttribute("href", "/");
    expect(navigation.querySelector("[data-titlebar]")?.nextElementSibling).toContainElement(brand);
  });
});
