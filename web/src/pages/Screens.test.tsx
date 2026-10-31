import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { config, noTeam, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

describe("Organisation", () => {
  it("says why there is no org chart yet and what to look at", async () => {
    mockApi(shell);
    const { container } = renderAt("/org");
    expect(await screen.findByText("hz system capability list")).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("shows how to join or start a team when there is none", async () => {
    mockApi({ ...shell, "/api/team/show": noTeam });
    renderAt("/team");
    expect(await screen.findByText("hz team join <url>")).toBeInTheDocument();
    expect(screen.getByText("hz team new <name>")).toBeInTheDocument();
  });
});

describe("Profile", () => {
  it("lists who you are", async () => {
    mockApi(shell);
    const { container } = renderAt("/profile");
    expect(await screen.findByText("engineering-manager")).toBeInTheDocument();
    expect(screen.getByText("Europe/Berlin")).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("shows the team name the person gave, and asks for one when there is none", async () => {
    mockApi({
      ...shell,
      "/api/system/config/show": {
        ...config,
        settings: [
          ...config.settings,
          {
            section: "team",
            key: "name",
            value: "Payments",
            default: null,
            source: "user",
            env: "HZ_TEAM__NAME",
            description: "Team name.",
          },
        ],
      },
    });
    renderAt("/profile");
    expect(await screen.findByText("Payments")).toBeInTheDocument();
  });

  it("links an unset team name to its setting", async () => {
    mockApi(shell);
    renderAt("/profile");
    await screen.findByText("engineering-manager");
    expect(screen.getByText("Team").closest("div")).toHaveTextContent("not set");
  });
});
