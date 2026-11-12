import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";

const playbook = {
  module: "tracker",
  summary: "Keeps track of issues",
  assets: [
    {
      name: "issue-list",
      description: "Open issues of a project",
      kept_by: "tracker",
      hints: [{ read: "tracker.issues", summary: "Reads the issue list" }],
    },
    { name: "board", description: "A board", kept_by: null, hints: [] },
  ],
};

const candidate = (asset: string, ref: string, url: string | null = null) => ({
  asset,
  ref,
  title: `Title of ${ref}`,
  playbook: "tracker",
  hint: "tracker.issues",
  url,
});

describe("The artifact types screen", () => {
  beforeEach(() => sessionStorage.setItem("hz.token", "test-token"));

  it("lists every playbook's types with what keeps them and how they are found", async () => {
    mockApi({ ...shell, "/api/heinzel/playbooks": { playbooks: [playbook], next: [] } });
    renderAt("/inventory/types");
    const section = within(await screen.findByRole("region", { name: /tracker/ }));
    expect(section.getByText("issue-list")).toBeInTheDocument();
    expect(section.getByText("Open issues of a project")).toBeInTheDocument();
    expect(section.getByText("tracker.issues")).toHaveAttribute("title", "Reads the issue list");
    expect(section.getByText("board")).toBeInTheDocument();
    expect(section.getAllByText("tracker")).not.toHaveLength(0);
  });

  it("points to plugin search when no playbook is installed", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/playbooks": { playbooks: [], next: [{ command: "hz system plugin add x", why: "" }] },
    });
    renderAt("/inventory/types");
    expect(await screen.findByText(/No playbooks installed/)).toBeInTheDocument();
    expect(screen.getByText("hz system plugin add x")).toBeInTheDocument();
    expect(screen.getByText("hz system plugin search")).toBeInTheDocument();
  });
});

describe("The artifacts screen", () => {
  beforeEach(() => sessionStorage.setItem("hz.token", "test-token"));

  it("looks for candidates only once the person asks", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/discover": { candidates: [], problems: [], next: [] },
    });
    renderAt("/inventory/assets");
    await screen.findByRole("button", { name: "Find candidates" });
    expect(calls.some((call) => call.url.includes("/heinzel/discover"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Find candidates" }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/heinzel/discover"))).toBe(true));
    expect(await screen.findByText("No candidates found.")).toBeInTheDocument();
  });

  it("shows each candidate with its source and an Open link only when it has a URL", async () => {
    const user = userEvent.setup();
    mockApi({
      ...shell,
      "/api/heinzel/discover": {
        candidates: [candidate("issue-list", "ACME-1", "https://tracker.example/ACME-1"), candidate("board", "B-2")],
        problems: [],
        next: [],
      },
    });
    renderAt("/inventory/assets");
    await user.click(await screen.findByRole("button", { name: "Find candidates" }));
    const first = (await screen.findByText("Title of ACME-1")).closest("li");
    const second = screen.getByText("Title of B-2").closest("li");
    if (!first || !second) throw new Error("expected both candidates");
    expect(within(first).getByRole("link", { name: "Open it" })).toHaveAttribute(
      "href",
      "https://tracker.example/ACME-1",
    );
    expect(within(first).getByText("ACME-1")).toBeInTheDocument();
    expect(within(second).queryByRole("link")).not.toBeInTheDocument();
  });

  it("says what could not be read next to the candidates found", async () => {
    const user = userEvent.setup();
    mockApi({
      ...shell,
      "/api/heinzel/discover": {
        candidates: [candidate("issue-list", "ACME-1")],
        problems: [{ playbook: "tracker", asset: "board", hint: "tracker.boards", problem: "Not signed in" }],
        next: [],
      },
    });
    renderAt("/inventory/assets");
    await user.click(await screen.findByRole("button", { name: "Find candidates" }));
    expect(await screen.findByText("board (tracker.boards): Not signed in")).toBeInTheDocument();
    expect(screen.getByText("Title of ACME-1")).toBeInTheDocument();
  });
});
