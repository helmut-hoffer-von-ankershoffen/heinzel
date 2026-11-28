import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { everyday, shell, theme, themeList } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";

/** The swarm's figures inside `root`: the product mark is a team, never one Heinzel. */
const figures = (root: HTMLElement) =>
  within(root).getByTestId("heinzel-swarm").querySelectorAll(":scope > g[transform]").length;

describe("Sidebar header", () => {
  it("keeps Close next to the navigation controls in the built-in mobile drawer", async () => {
    const width = window.innerWidth;
    vi.stubGlobal("heinzelDesktop", { mobileWorkspace: true });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    try {
      mockApi({ ...shell, "/api/system/expert": everyday, "/api/system/theme/list": themeList("default") });
      const user = userEvent.setup();
      renderAt("/doctor");
      await user.click(screen.getByRole("button", { name: "Open navigation" }));
      const drawer = await screen.findByRole("dialog", { name: "Main" });
      const home = await within(drawer).findByRole("link", { name: "Today" });
      const close = within(drawer).getByRole("button", { name: "Close" });
      expect(home).toHaveAttribute("href", "/");
      expect(close.parentElement).toBe(home.parentElement);
      expect(within(drawer).queryByRole("link", { name: "Heinzelmännchen home" })).toBeNull();
      expect(within(drawer).queryByRole("button", { name: "Collapse navigation" })).toBeNull();
      await user.click(close);
      expect(screen.queryByRole("dialog", { name: "Main" })).toBeNull();
      // Native dialog focus restoration is checked in the browser fixture.
    } finally {
      vi.unstubAllGlobals();
      Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    }
  });

  it("omits the built-in brand row, keeps the team link and preserves the collapsed rail mark", async () => {
    mockApi({
      ...shell,
      "/api/system/expert": everyday,
      "/api/system/theme/list": themeList("default", [theme()]),
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const navigation = screen.getByRole("complementary", { name: "Main" });
    const home = await within(navigation).findByRole("link", { name: "Today" });
    expect(home).toHaveAttribute("href", "/");
    expect(within(navigation).queryByTestId("heinzel-wordmark")).toBeNull();
    // The Heinzels screen is several Heinzels too: its navigation icon is the team glyph, not one face.
    const heinzels = await within(navigation).findByRole("link", { name: "Heinzelmännchen" });
    expect(heinzels).toHaveAttribute("href", "/heinzel");
    expect(within(heinzels).getByTestId("heinzel-team")).toBeInTheDocument();
    // The collapse button is the top bar's icon button (36 px) with a 44 px hit area around it.
    const collapse = within(navigation).getByRole("button", { name: "Collapse navigation" });
    expect(collapse.parentElement).toBe(home.parentElement);
    expect(collapse).toHaveClass("size-9", "before:-inset-1");
    await expectAccessible(navigation);
    await user.click(collapse);
    const expand = within(navigation).getByRole("button", { name: "Expand navigation" });
    expect(within(expand).getByTestId("heinzel-swarm")).toHaveClass("size-7");
    expect(figures(expand)).toBe(5);
    expect(within(navigation).getByRole("link", { name: "Heinzelmännchen" })).toContainElement(
      within(navigation).getByTestId("heinzel-team"),
    );
  });
});
