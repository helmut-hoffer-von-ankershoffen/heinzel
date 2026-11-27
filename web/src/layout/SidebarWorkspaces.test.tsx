import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";

async function openProfile(inDrawer = false) {
  const user = userEvent.setup();
  mockApi(shell);
  renderAt("/");
  if (inDrawer) await user.click(await screen.findByRole("button", { name: "Open navigation" }));
  const profile = await screen.findByTitle("Mira Example · engineering manager");
  await user.click(profile);
  return { user, profile };
}

it.each([
  ["browser", undefined],
  ["Electron", { titlebar: { height: 44, left: 80, right: 0 } }],
  ["iPhone", { mobileWorkspace: true }],
])("preserves the existing %s profile menu without a native workspace action", async (platform, bridge) => {
  vi.stubGlobal("heinzelDesktop", bridge);
  if (platform === "iPhone") vi.stubGlobal("innerWidth", 390);
  await openProfile(platform === "iPhone");
  expect(screen.queryByRole("menuitem", { name: /Workspaces/ })).toBeNull();
  expect(screen.getByRole("menuitem", { name: /^Settings/ }).nextElementSibling).toBe(
    screen.getByRole("menuitem", { name: "Profile" }),
  );
});

it.each(["Mac", "iPhone"])(
  "reads the current %s selection on each opening and opens the native sheet",
  async (platform) => {
    let resolveFirst!: (name: string) => void;
    const first = new Promise<string>((resolve) => {
      resolveFirst = resolve;
    });
    const currentName = vi.fn().mockReturnValueOnce(first).mockResolvedValue("River Studio");
    const open = vi.fn().mockResolvedValue(true);
    const mobileWorkspace = platform === "iPhone";
    if (mobileWorkspace) vi.stubGlobal("innerWidth", 390);
    vi.stubGlobal("heinzelDesktop", { mobileWorkspace, workspaces: { currentName, open } });
    const { user } = await openProfile(mobileWorkspace);
    expect(currentName).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("menuitem", { name: "Workspaces…" })).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    const returnedProfile = await screen.findByTitle("Mira Example · engineering manager");
    await user.click(returnedProfile);
    await waitFor(() => expect(currentName).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("River Studio")).toBeVisible();
    const item = await screen.findByRole("menuitem", { name: "Workspaces… River Studio" });
    expect(screen.getByRole("menuitem", { name: /^Settings/ }).nextElementSibling).toBe(item);
    expect(item.nextElementSibling).toBe(screen.getByRole("menuitem", { name: "Profile" }));
    await act(async () => resolveFirst("Previous workspace"));
    expect(item).toHaveAccessibleName("Workspaces… River Studio");
    item.focus();
    await user.keyboard("{Enter}");
    expect(open).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(returnedProfile).toHaveFocus();
  },
);

it("keeps the generic Mac action usable after a failed name lookup or sheet request", async () => {
  const currentName = vi.fn().mockRejectedValue(new Error("Workspace name unavailable"));
  const open = vi
    .fn()
    .mockRejectedValueOnce(new Error("App busy"))
    .mockResolvedValueOnce(false)
    .mockResolvedValue(true);
  vi.stubGlobal("heinzelDesktop", { workspaces: { currentName, open } });
  const { user } = await openProfile();
  const item = screen.getByRole("menuitem", { name: "Workspaces…" });
  for (let attempt = 0; attempt < 2; attempt++) {
    await user.click(item);
    await waitFor(() => expect(item).toBeEnabled());
    expect(item).toBeVisible();
  }
  await user.click(item);
  expect(open).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole("menu")).toBeNull();
});
