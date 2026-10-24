import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { everyday, heinzelmaennchen, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

describe("Heinzelmännchen", () => {
  it("lists each Heinzelmännchen with its contract, owner, state and runs", async () => {
    mockApi(shell);
    const { container } = renderAt("/heinzel");
    const table = within(await screen.findByRole("table"));
    const row = table.getByRole("link", { name: "Weekly digest" }).closest("tr") as HTMLElement;
    expect(within(row).getByText("weekly-digest.prose.md")).toBeInTheDocument();
    expect(within(row).getByText("You")).toBeInTheDocument();
    expect(within(row).getByText("scheduled")).toBeInTheDocument();
    expect(within(row).getByText("hz heinzel drafts --name me:weekly-digest")).toBeInTheDocument();
    const team = table.getByRole("link", { name: "Team report" }).closest("tr") as HTMLElement;
    expect(within(team).getByText("Team")).toBeInTheDocument();
    expect(within(team).getByText("blocked")).toBeInTheDocument();
    expect(within(team).getByText("never")).toBeInTheDocument();
    expect(within(team).getByText("when you wake it")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Heinzelmännchen" })).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("links a Heinzelmännchen to its contract's page", async () => {
    mockApi(shell);
    renderAt("/heinzel");
    const link = within(await screen.findByRole("table")).getByRole("link", { name: "Weekly digest" });
    expect(link.getAttribute("href")).toBe("/contracts/me%3Aweekly-digest");
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
