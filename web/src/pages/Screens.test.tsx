import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { graph, noTeam, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { layout } from "./Contracts";

describe("Contract graph", () => {
  it("lays out sources, contracts and assets left to right", () => {
    const { nodes, edges } = layout(graph);
    const x = (id: string) => nodes.find((node) => node.id === id)?.position.x ?? Number.NaN;
    expect(x("tracker.issues(project=ACME)")).toBeLessThan(x("me:weekly-digest"));
    expect(x("me:weekly-digest")).toBeLessThan(x("contract:me/weekly-digest#digest"));
    expect(edges).toHaveLength(2);
  });

  it("shows the command that starts a contract when there are none", async () => {
    mockApi({
      ...shell,
      "/api/workflow/graph": { nodes: [], edges: [], cycles: [], unmaintained: [], mermaid: "", next: [] },
    });
    const { container } = renderAt("/contracts");
    expect(await screen.findByText("hz workflow new weekly-digest")).toBeInTheDocument();
    await expectAccessible(container);
  });
});

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
});
