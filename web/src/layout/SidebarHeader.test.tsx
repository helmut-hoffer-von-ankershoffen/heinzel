import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { shell, theme, themeList } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

describe("Sidebar header", () => {
  it("is the product lockup, the way home, beside the top bar's collapse button; the rail keeps the mark", async () => {
    mockApi({ ...shell, "/api/system/theme/list": themeList("default", [theme()]) });
    const user = userEvent.setup();
    renderAt("/doctor");
    const navigation = screen.getByRole("complementary", { name: "Main" });
    const lockup = await within(navigation).findByTestId("heinzel-wordmark");
    // The swarm mark (the product's logo), never one Heinzel's bust, and the name as text beside it.
    expect(within(lockup).getByTestId("heinzel-swarm").querySelectorAll(":scope > g[transform]")).toHaveLength(5);
    expect(within(lockup).queryByTestId("heinzel-icon")).not.toBeInTheDocument();
    expect(lockup).toHaveTextContent("Heinzelmännchen");
    expect(lockup.querySelector("span.truncate")).toHaveClass("font-semibold");
    // The lockup links home and is named by its wordmark, not by the picture.
    const home = lockup.closest("a");
    expect(home).toHaveAttribute("href", "/");
    expect(home).toHaveAccessibleName("Heinzelmännchen");
    // The collapse button is the top bar's icon button (36 px) with a 44 px hit area around it.
    const collapse = within(navigation).getByRole("button", { name: "Collapse navigation" });
    expect(collapse).toHaveClass("size-9", "before:-inset-1");
    await expectAccessible(navigation);
    await user.click(collapse);
    const expand = within(navigation).getByRole("button", { name: "Expand navigation" });
    expect(within(expand).getByTestId("heinzel-swarm")).toHaveClass("size-7");
  });
});
