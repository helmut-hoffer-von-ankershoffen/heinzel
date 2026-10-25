import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import document from "@/test/fixtures/graph.jgf.json";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const withGraph = { ...shell, "/api/graph": document };

/** The graph's cards, once elk placed them. */
const card = (name: RegExp) => screen.findByRole("link", { name }, { timeout: 5000 });

describe("the Team graph screen", () => {
  it("says how a team graph comes about while `hz graph` answers 404", async () => {
    mockApi(shell);
    const { container } = renderAt("/graph");
    expect(
      await screen.findByText("No team graph yet — Heinzelmännchen report what they read and write; run:"),
    ).toBeInTheDocument();
    expect(screen.getByText("hz graph")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Copy as Mermaid/ })).not.toBeInTheDocument();
    await expectAccessible(container);
  });

  it("doesn't ask for a graph the server has no module for", async () => {
    const { calls } = mockApi({ ...shell, "/api/system/info": { version: "0.4.0", modules: [] } });
    renderAt("/graph");
    expect(await screen.findByText("hz graph")).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes("/api/graph"))).toBe(false);
  });

  it("says so when the server fails, rather than showing an empty graph", async () => {
    mockApi({ ...shell, "/api/graph": { status: 500, body: { error: "the store is locked" } } });
    renderAt("/graph");
    expect(await screen.findByRole("alert", {}, { timeout: 5000 })).toHaveTextContent(/the store is locked/);
  });

  it("heads the graph with how the flow is doing, and draws each state in words", async () => {
    mockApi(withGraph);
    const { container } = renderAt("/graph");
    expect(await screen.findByText("Weekly report waiting on 2 updates")).toBeInTheDocument();
    const costas = await card(/^Tracker update: costas.*Offline/);
    expect(costas).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Tracker update: priya.*Waiting for approval/ })).toBeInTheDocument();
    // Each promise, for screen readers too.
    const promises = screen.getByRole("list", { name: "Promises" });
    expect(within(promises).getByText("mira-report reads Tracker update: Offline")).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("explains every state and kind of node in the legend", async () => {
    mockApi(withGraph);
    renderAt("/graph");
    const states = await screen.findByRole("list", { name: "Promise states" });
    expect(
      within(states)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(["On time", "Waiting for approval", "Late", "Fell back", "Offline"]);
    const kinds = screen.getByRole("list", { name: "Kinds of node" });
    expect(within(kinds).getByText("Heinzelmännchen")).toBeInTheDocument();
    expect(within(kinds).getByText("Artifact with parts")).toBeInTheDocument();
  });

  it("draws the tracker as a group around its rows", async () => {
    mockApi(withGraph);
    const { container } = renderAt("/graph");
    const tracker = await screen.findByText("Tracker update", {}, { timeout: 5000 }); // nowhere to open: no link
    const group = tracker.closest(".react-flow__node");
    expect(group).toHaveClass("react-flow__node-teamGroup");
    await card(/^Tracker update: priya/);
    const rows = [...container.querySelectorAll(".react-flow__node-team")].map((node) => node.textContent);
    expect(rows.some((text) => text?.startsWith("Tracker update: priya"))).toBe(true);
  });

  it("opens an artifact where it lives in a new tab, a Heinzelmännchen on its page or in the chat", async () => {
    mockApi(withGraph);
    renderAt("/graph");
    const row = await card(/^Tracker update: costas/);
    expect(row).toHaveAttribute("href", "https://tracker.acme.example/boards/7#costas");
    expect(row).toHaveAttribute("target", "_blank");
    expect(row).toHaveAttribute("rel", "noreferrer noopener");
    expect(screen.getByRole("link", { name: /^priya-tracker/ })).toHaveAttribute("href", "/heinzel/priya-tracker");
    expect(screen.getByRole("link", { name: "Ask about priya-tracker in the chat" })).toHaveAttribute(
      "href",
      "/graph?chat-context=heinzel%3Apriya-tracker",
    );
    await userEvent.click(screen.getByRole("link", { name: /^priya-tracker/ }));
    // No page for a Heinzelmännchen yet: the route is there to link to.
    expect(await screen.findByRole("link", { name: "Go home" })).toBeInTheDocument();
  });

  it("copies the graph as Mermaid", async () => {
    mockApi(withGraph);
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    renderAt("/graph");
    await user.click(await screen.findByRole("button", { name: "Copy as Mermaid" }));
    expect(writeText).toHaveBeenCalledOnce();
    const mermaid = writeText.mock.calls[0]?.[0] ?? "";
    expect(mermaid).toMatch(/^flowchart LR\n/);
    expect(mermaid).toContain('subgraph n0["Tracker update"]');
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });
});
