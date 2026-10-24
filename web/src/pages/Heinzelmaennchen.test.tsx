import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { everyday, heinzelmaennchen, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

describe("Heinzelmännchen", () => {
  it("lists each Heinzelmännchen with its contract, owner, state and runs", async () => {
    mockApi(shell);
    const { container } = renderAt("/heinzel");
    const row = (await screen.findByRole("link", { name: "Weekly digest" })).closest("tr") as HTMLElement;
    expect(within(row).getByText("weekly-digest.prose.md")).toBeInTheDocument();
    expect(within(row).getByText("You")).toBeInTheDocument();
    expect(within(row).getByText("scheduled")).toBeInTheDocument();
    expect(within(row).getByText("hz heinzel drafts --name me:weekly-digest")).toBeInTheDocument();
    const team = screen.getByRole("link", { name: "Team report" }).closest("tr") as HTMLElement;
    expect(within(team).getByText("Team")).toBeInTheDocument();
    expect(within(team).getByText("blocked")).toBeInTheDocument();
    expect(within(team).getByText("never")).toBeInTheDocument();
    expect(within(team).getByText("when you wake it")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Heinzelmännchen" })).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("links a Heinzelmännchen to its contract's page", async () => {
    mockApi(shell);
    const user = userEvent.setup();
    renderAt("/heinzel");
    await user.click(await screen.findByRole("link", { name: "Weekly digest" }));
    expect(await screen.findByRole("heading", { name: "Weekly digest" })).toBeInTheDocument();
  });

  it("says how to start one when there is none", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/list": {
        ...heinzelmaennchen,
        heinzelmaennchen: [],
        next: [{ command: "hz heinzel new my-first-heinzel", why: "a starter contract" }],
      },
    });
    renderAt("/heinzel");
    expect(await screen.findByText("hz heinzel new my-first-heinzel")).toBeInTheDocument();
  });

  it("is in the navigation for everyone, the rail included", async () => {
    mockApi({ ...shell, "/api/system/expert": everyday });
    renderAt("/");
    const links = await screen.findAllByRole("link", { name: "Heinzelmännchen" });
    expect(links.every((link) => link.getAttribute("href") === "/heinzel")).toBe(true);
  });
});
