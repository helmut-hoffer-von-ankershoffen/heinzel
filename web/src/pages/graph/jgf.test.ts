import { describe, expect, it } from "vitest";
import document from "@/test/fixtures/graph.jgf.json";
import { summarise } from "./GraphView";
import { fromJgf, groupIds, heinzelId, pending, toMermaid } from "./jgf";
import { layoutTeam } from "./layout";

const team = () => fromJgf(document);

describe("fromJgf", () => {
  it("reads the nodes, their kinds, states and groups", () => {
    const graph = team();
    expect(graph.label).toBe("Team graph");
    expect(graph.nodes).toHaveLength(8);
    const costas = graph.nodes.find((node) => node.id === "contract:team/tracker-update#row/costas");
    expect(costas).toMatchObject({
      kind: "artifact",
      state: "offline",
      owner: "costas",
      contractVersion: "1.0.0",
      parent: "contract:team/tracker-update#row",
    });
    expect(graph.nodes.find((node) => node.id === "heinzel:priya-tracker")?.kind).toBe("heinzel");
    expect([...groupIds(graph)]).toEqual(["contract:team/tracker-update#row"]);
  });

  it("points every edge the way data flows: from what is read, to what is written", () => {
    const edges = team().edges;
    const read = edges.find((edge) => edge.relation === "reads" && edge.state === "offline");
    expect(read).toMatchObject({ from: "contract:team/tracker-update#row", to: "heinzel:mira-report" });
    const write = edges.find((edge) => edge.from === "heinzel:mira-report");
    expect(write).toMatchObject({
      to: "contract:team/weekly-report#report",
      relation: "writes",
      lastVersion: "2026-W42",
    });
  });

  it("reads a reads edge the server already pointed from the artifact the same way", () => {
    const graph = fromJgf({
      graph: {
        nodes: { a: { metadata: { kind: "artifact" } }, h: { metadata: { kind: "heinzel" } } },
        edges: [{ source: "a", target: "h", relation: "reads" }],
      },
    });
    expect(graph.edges[0]).toMatchObject({ from: "a", to: "h", state: null });
  });

  it("leaves out what it can't read instead of failing", () => {
    const graph = fromJgf({
      graph: {
        nodes: {
          x: { label: "X", metadata: { state: "great", parent: "gone", url: "javascript:alert(1)" } },
          y: { metadata: { parent: "z" } },
          z: { metadata: { parent: "y" } },
        },
        edges: [{ source: "x", target: "nowhere" }, { target: "x" }],
      },
    });
    expect(graph.nodes[0]).toMatchObject({
      label: "X",
      kind: "artifact",
      state: null,
      parent: undefined,
      url: undefined,
    });
    expect(graph.nodes.every((node) => node.parent === undefined || node.id === "z")).toBe(true);
    expect(graph.edges).toEqual([]);
    expect(fromJgf(null)).toEqual({ label: undefined, summary: undefined, nodes: [], edges: [] });
    expect(fromJgf({ graph: { nodes: [{ id: "a", label: "A" }] } }).nodes[0]?.label).toBe("A");
  });

  it("takes a Heinzelmännchen's id for its page without the prefix", () => {
    expect(heinzelId("heinzel:priya")).toBe("priya");
    expect(heinzelId("priya")).toBe("priya");
  });
});

describe("the flow summary", () => {
  it("counts the updates the flow waits on, never who is behind", () => {
    expect(pending(team())).toEqual({ waiting: 2, late: 0 });
    const summary = summarise(team());
    expect(summary).toEqual({ text: "Weekly report waiting on 2 updates", tone: "warn" });
    expect(summary.text).not.toMatch(/Costas|Priya/);
  });

  it("says when every promise is kept, and how many are late", () => {
    const graph = { ...team(), summary: undefined };
    const kept = { ...graph, edges: graph.edges.map((edge) => ({ ...edge, state: "on_time" as const })) };
    expect(summarise(kept)).toEqual({ text: "Team graph: every contract kept", tone: "ok" });
    const late = {
      ...graph,
      edges: graph.edges.map((edge) => (edge.relation === "writes" ? { ...edge, state: "late" as const } : edge)),
    };
    expect(summarise(late)).toEqual({ text: "Team graph waiting on 3 updates · 3 late", tone: "bad" });
    expect(summarise({ ...graph, summary: "Said by the server" }).text).toBe("Said by the server");
  });
});

describe("toMermaid", () => {
  it("writes groups as subgraphs and colours promises by state", () => {
    const mermaid = toMermaid(team());
    expect(mermaid.startsWith("flowchart LR\n")).toBe(true);
    expect(mermaid).toContain('subgraph n0["Tracker update"]');
    expect(mermaid).toContain('(["priya-tracker"])');
    expect(mermaid).toMatch(/-->\|reads\|/);
    expect(mermaid).toMatch(/class [n0-9,]+ waiting_for_approval/);
  });

  it("dashes a fallback, dots offline, and escapes quotes", () => {
    const mermaid = toMermaid({
      nodes: [
        { id: "a", label: 'The "big" doc', kind: "artifact", state: "offline" },
        { id: "h", label: "H", kind: "heinzel", state: "fell_back" },
      ],
      edges: [{ id: "e", from: "h", to: "a", relation: "writes", state: "fell_back" }],
    });
    expect(mermaid).toContain('n0["The #quot;big#quot; doc"]');
    expect(mermaid).toContain("n1 -.->|writes| n0");
    expect(mermaid).toContain("linkStyle 0 stroke:#8a5c00,stroke-width:2px,stroke-dasharray:6 4");
    expect(mermaid).toContain("classDef offline stroke:#8a8a8a,stroke-width:2px,stroke-dasharray:2 4");
  });
});

describe("layoutTeam", () => {
  it("lays the flow out left to right, rows inside their tracker", async () => {
    const { nodes, edges } = await layoutTeam(team());
    expect(edges).toHaveLength(5);
    const at = (id: string) => nodes.find((node) => node.id === id);
    const tracker = at("contract:team/tracker-update#row");
    const row = at("contract:team/tracker-update#row/costas");
    expect(tracker?.type).toBe("teamGroup");
    expect(row?.parentId).toBe("contract:team/tracker-update#row");
    // Parents come before their children, and children sit inside them.
    expect(nodes.indexOf(tracker as never)).toBeLessThan(nodes.indexOf(row as never));
    expect(row?.position.x).toBeGreaterThanOrEqual(0);
    expect((row?.position.x ?? 0) + (row?.width ?? 0)).toBeLessThanOrEqual(tracker?.width ?? 0);
    // Writers left of the tracker, the reader right of it.
    expect(at("heinzel:costas-tracker")?.position.x).toBeLessThan(tracker?.position.x ?? 0);
    expect(at("heinzel:mira-report")?.position.x).toBeGreaterThan(tracker?.position.x ?? 0);
  });
});
