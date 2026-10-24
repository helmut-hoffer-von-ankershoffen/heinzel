// The team graph as `hz graph` answers it, JSON Graph Format v2 (https://jsongraphformat.info), turned into the
// model the screen draws: Heinzelmännchen and the artifacts they read and write, each promise with its state.
// A thin converter: it only checks shapes, normalises the direction data flows in, and finds group nodes. Pure, so
// the screen, the Mermaid export and the tests see the same graph.

export const STATES = ["on_time", "waiting_for_approval", "late", "fell_back", "offline"] as const;
/** Whether a promise is kept: on time, waiting for its owner's approval, late, kept by a fallback, or offline. */
export type PromiseState = (typeof STATES)[number];
export type NodeKind = "artifact" | "heinzel";
export type Relation = "reads" | "writes";

export type TeamNode = {
  id: string;
  label: string;
  kind: NodeKind;
  state: PromiseState | null;
  owner?: string;
  contractVersion?: string;
  deadline?: string;
  url?: string;
  /** The group node this one sits in (a tracker's row sits in the tracker). */
  parent?: string;
};

export type TeamEdge = {
  id: string;
  /** Where the data comes from: the artifact a Heinzelmännchen reads, or the one that writes. */
  from: string;
  /** Where the data goes. */
  to: string;
  relation: Relation;
  state: PromiseState | null;
  declared?: string;
  observed?: string;
  lastVersion?: string;
};

export type TeamGraph = {
  label?: string;
  /** The flow summary the server wrote, when it wrote one; otherwise the screen says it (`summarise`). */
  summary?: string;
  nodes: TeamNode[];
  edges: TeamEdge[];
};

/** The JSON Graph Format v2 document `GET /api/graph` answers with (`type: "hz-team"`). */
export type JgfDocument = {
  graph: {
    directed?: boolean;
    type?: string;
    label?: string;
    metadata?: Record<string, unknown>;
    nodes?: Record<string, { label?: string; metadata?: Record<string, unknown> }>;
    edges?: { id?: string; source: string; target: string; relation?: string; metadata?: Record<string, unknown> }[];
  };
};

const text = (value: unknown): string | undefined =>
  typeof value === "string" && value !== "" ? value : typeof value === "number" ? String(value) : undefined;

const stateOf = (value: unknown): PromiseState | null =>
  STATES.includes(value as PromiseState) ? (value as PromiseState) : null;

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

/** The model of a JGF document; nodes and edges it can't read are left out rather than failing the screen. */
export function fromJgf(document: unknown): TeamGraph {
  const graph = record(record(document).graph);
  const rawNodes = graph.nodes;
  // JGF v2 keys nodes by id; v1 lists them with an id each. Both read.
  const entries: [string, Record<string, unknown>][] = Array.isArray(rawNodes)
    ? rawNodes.flatMap((node) => {
        const id = text(record(node).id);
        return id ? [[id, record(node)] as [string, Record<string, unknown>]] : [];
      })
    : Object.entries(record(rawNodes)).map(([id, node]) => [id, record(node)]);

  const nodes: TeamNode[] = entries.map(([id, node]) => {
    const metadata = record(node.metadata);
    return {
      id,
      label: text(node.label) ?? id,
      kind: metadata.kind === "heinzel" ? "heinzel" : "artifact",
      state: stateOf(metadata.state),
      owner: text(metadata.owner),
      contractVersion: text(metadata.contract_version),
      deadline: text(metadata.deadline),
      url: safeUrl(text(metadata.url)),
      parent: text(metadata.parent),
    };
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  // A parent must exist, and following parents must not come back round.
  for (const node of nodes) {
    const seen = new Set([node.id]);
    let parent = node.parent;
    while (parent !== undefined) {
      if (!byId.has(parent) || seen.has(parent)) {
        node.parent = undefined;
        break;
      }
      seen.add(parent);
      parent = byId.get(parent)?.parent;
    }
  }

  const edges: TeamEdge[] = [];
  const rawEdges = Array.isArray(graph.edges) ? graph.edges : [];
  for (const [index, raw] of rawEdges.entries()) {
    const edge = record(raw);
    const source = text(edge.source);
    const target = text(edge.target);
    if (!source || !target || !byId.has(source) || !byId.has(target)) continue;
    const relation: Relation = edge.relation === "reads" ? "reads" : "writes";
    // Data flows from what is read to who reads it, whichever end the server named the source.
    const reversed =
      relation === "reads" && byId.get(source)?.kind === "heinzel" && byId.get(target)?.kind !== "heinzel";
    const metadata = record(edge.metadata);
    edges.push({
      id: text(edge.id) ?? `${source}->${target}#${index}`,
      from: reversed ? target : source,
      to: reversed ? source : target,
      relation,
      state: stateOf(metadata.state),
      declared: text(metadata.declared),
      observed: text(metadata.observed),
      lastVersion: text(metadata.last_version),
    });
  }
  const metadata = record(graph.metadata);
  return { label: text(graph.label) ?? text(metadata.label), summary: text(metadata.summary), nodes, edges };
}

/** Only web links open from the graph; anything else (`javascript:`, a file) is dropped. */
function safeUrl(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}

/** The ids of nodes other nodes sit in. */
export function groupIds(graph: TeamGraph): Set<string> {
  return new Set(graph.nodes.flatMap((node) => (node.parent ? [node.parent] : [])));
}

/** The id a Heinzelmännchen's page and chat take: its graph id without the `heinzel:` prefix. */
export const heinzelId = (id: string) => id.replace(/^heinzel:/, "");

/** The promises the flow still waits on: writes upstream of its ends that aren't kept yet, and how many are late. */
export function pending(graph: TeamGraph): { waiting: number; late: number } {
  const feeds = new Map<string, TeamEdge[]>();
  for (const edge of graph.edges) feeds.set(edge.to, [...(feeds.get(edge.to) ?? []), edge]);
  const children = new Map<string, string[]>();
  for (const node of graph.nodes) {
    if (node.parent) children.set(node.parent, [...(children.get(node.parent) ?? []), node.id]);
  }
  const leaves = new Set(graph.edges.map((edge) => edge.from));
  const ends = graph.nodes.filter((node) => !leaves.has(node.id) && feeds.has(node.id));
  // Walk back from the ends; reading a group reads its rows too.
  const seen = new Set<string>();
  const stack = ends.map((node) => node.id);
  const upstream = new Set<TeamEdge>();
  while (stack.length > 0) {
    const id = stack.pop() as string;
    if (seen.has(id)) continue;
    seen.add(id);
    for (const edge of feeds.get(id) ?? []) {
      upstream.add(edge);
      stack.push(edge.from);
    }
    stack.push(...(children.get(id) ?? []));
  }
  const open = [...upstream].filter(
    (edge) =>
      edge.relation === "writes" &&
      (edge.state === "waiting_for_approval" || edge.state === "late" || edge.state === "offline"),
  );
  return { waiting: open.length, late: open.filter((edge) => edge.state === "late").length };
}

// Mermaid can't read CSS variables: the light theme's colours (index.css), close enough in a dark document too.
const MERMAID_COLOURS: Record<PromiseState, string> = {
  on_time: "#1f7444",
  waiting_for_approval: "#8a5c00",
  late: "#b4372d",
  fell_back: "#8a5c00",
  offline: "#8a8a8a",
};
const MERMAID_DASH: Partial<Record<PromiseState, string>> = { fell_back: "6 4", offline: "2 4" };

const quote = (label: string) => `"${label.replaceAll('"', "#quot;")}"`;

/** The graph as a Mermaid flowchart, for documents and pull requests: groups as subgraphs, promises coloured. */
export function toMermaid(graph: TeamGraph): string {
  const alias = new Map(graph.nodes.map((node, index) => [node.id, `n${index}`]));
  const groups = groupIds(graph);
  const lines = ["flowchart LR"];
  const place = (parent: string | undefined, depth: number) => {
    for (const node of graph.nodes.filter((candidate) => candidate.parent === parent)) {
      const pad = "  ".repeat(depth);
      const name = alias.get(node.id) as string;
      if (groups.has(node.id)) {
        lines.push(`${pad}subgraph ${name}[${quote(node.label)}]`);
        place(node.id, depth + 1);
        lines.push(`${pad}end`);
      } else {
        lines.push(
          node.kind === "heinzel" ? `${pad}${name}([${quote(node.label)}])` : `${pad}${name}[${quote(node.label)}]`,
        );
      }
    }
  };
  place(undefined, 1);
  for (const edge of graph.edges) {
    const arrow = edge.state === "fell_back" || edge.state === "offline" ? "-.->" : "-->";
    lines.push(`  ${alias.get(edge.from)} ${arrow}|${edge.relation}| ${alias.get(edge.to)}`);
  }
  for (const state of STATES) {
    const colour = MERMAID_COLOURS[state];
    const dash = MERMAID_DASH[state] ? `,stroke-dasharray:${MERMAID_DASH[state]}` : "";
    const members = graph.nodes.filter((node) => node.state === state && !groups.has(node.id));
    if (members.length > 0) {
      lines.push(`  classDef ${state} stroke:${colour},stroke-width:2px${dash}`);
      lines.push(`  class ${members.map((node) => alias.get(node.id)).join(",")} ${state}`);
    }
    const links = graph.edges.flatMap((edge, index) => (edge.state === state ? [index] : []));
    if (links.length > 0) lines.push(`  linkStyle ${links.join(",")} stroke:${colour},stroke-width:2px${dash}`);
  }
  return `${lines.join("\n")}\n`;
}
