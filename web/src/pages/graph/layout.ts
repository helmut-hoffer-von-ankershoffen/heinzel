// The team graph placed left to right by elk's layered algorithm, groups laid out around what they contain, then
// handed to React Flow: group nodes first with their size, the nodes in them positioned relative to them.
// The bundled build runs elk on this thread, so no worker file is needed under the CSP (`worker-src 'self'`); it is
// loaded when the screen first lays a graph out, in a chunk of its own, so the rest of the app doesn't carry it.
import { type Edge, MarkerType, type Node } from "@xyflow/react";
import type { ELK, ElkExtendedEdge, ElkNode } from "elkjs/lib/elk.bundled.js";
import type { FlowTone } from "@/components/graph/FlowNode";
import { t } from "@/i18n";
import { groupIds, type PromiseState, type TeamGraph, type TeamNode } from "./jgf";

export const NODE_WIDTH = 248;
export const NODE_HEIGHT = 64;
/** Room above a group's contents for its header. */
const GROUP_HEADER = 44;

export type TeamNodeData = { node: TeamNode; tone: FlowTone; stateLabel?: string };
export type TeamFlowNode = Node<TeamNodeData, "team" | "teamGroup">;

export const stateTone: Record<PromiseState, FlowTone> = {
  on_time: "ok",
  waiting_for_approval: "warn",
  late: "bad",
  fell_back: "warn",
  offline: "neutral",
};

/** How an edge of `state` is drawn: colour, and dashes for a fallback, dots for offline. */
export function edgeLook(state: PromiseState | null): { stroke: string; dash?: string; opacity: number } {
  switch (state) {
    case "on_time":
      return { stroke: "var(--color-ok)", opacity: 0.9 };
    case "waiting_for_approval":
      return { stroke: "var(--color-warn)", opacity: 1 };
    case "late":
      return { stroke: "var(--color-bad)", opacity: 1 };
    case "fell_back":
      return { stroke: "var(--color-warn)", dash: "6 4", opacity: 1 };
    case "offline":
      return { stroke: "var(--color-muted-foreground)", dash: "1 5", opacity: 0.8 };
    default:
      return { stroke: "var(--color-muted-foreground)", opacity: 0.55 };
  }
}

let engine: Promise<ELK> | undefined;
/** The layout engine, loaded once. */
function loadElk(): Promise<ELK> {
  engine ??= import("elkjs/lib/elk.bundled.js").then(({ default: Elk }) => new Elk());
  return engine;
}

const ROOT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.hierarchyHandling": "INCLUDE_CHILDREN",
  "elk.layered.spacing.nodeNodeBetweenLayers": "56",
  "elk.spacing.nodeNode": "24",
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
  "elk.padding": "[top=8,left=8,bottom=8,right=8]",
};
const GROUP_OPTIONS = { "elk.padding": `[top=${GROUP_HEADER},left=16,bottom=16,right=16]` };

export async function layoutTeam(graph: TeamGraph): Promise<{ nodes: TeamFlowNode[]; edges: Edge[] }> {
  const groups = groupIds(graph);
  // Sorted, so the same graph always gives the same picture.
  const ordered = [...graph.nodes].sort((a, b) => a.id.localeCompare(b.id));
  const elkNode = (node: TeamNode): ElkNode => {
    const inside = ordered.filter((child) => child.parent === node.id);
    return inside.length > 0
      ? { id: node.id, layoutOptions: GROUP_OPTIONS, children: inside.map(elkNode) }
      : { id: node.id, width: NODE_WIDTH, height: NODE_HEIGHT };
  };
  const edges: ElkExtendedEdge[] = graph.edges.map((edge) => ({
    id: edge.id,
    sources: [edge.from],
    targets: [edge.to],
  }));
  const elk = await loadElk();
  const placed = await elk.layout({
    id: "root",
    layoutOptions: ROOT_OPTIONS,
    children: ordered.filter((node) => !node.parent).map(elkNode),
    edges,
  });

  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const nodes: TeamFlowNode[] = [];
  // Parents before their children, as React Flow wants them; positions are relative to the parent, as elk's are.
  const visit = (children: ElkNode[] | undefined, parent: string | undefined) => {
    for (const child of children ?? []) {
      const node = byId.get(child.id);
      if (!node) continue;
      const group = groups.has(node.id);
      nodes.push({
        id: node.id,
        type: group ? "teamGroup" : "team",
        position: { x: child.x ?? 0, y: child.y ?? 0 },
        data: {
          node,
          tone: node.state ? stateTone[node.state] : "neutral",
          stateLabel: node.state ? t(`teamGraph.state.${node.state}`) : undefined,
        },
        ...(parent ? { parentId: parent, extent: "parent" as const } : {}),
        width: child.width ?? NODE_WIDTH,
        height: child.height ?? NODE_HEIGHT,
        style: { width: child.width ?? NODE_WIDTH, height: child.height ?? NODE_HEIGHT },
        // The link inside the node is what you focus and click; the node itself is only a place.
        focusable: false,
        selectable: false,
        draggable: false,
        connectable: false,
        ariaRole: "group",
        zIndex: group ? 0 : 1,
      });
      if (group) visit(child.children, node.id);
    }
  };
  visit(placed.children, undefined);

  const flowEdges: Edge[] = graph.edges.map((edge) => {
    const look = edgeLook(edge.state);
    return {
      id: edge.id,
      source: edge.from,
      target: edge.to,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed, color: look.stroke, width: 14, height: 14 },
      style: {
        stroke: look.stroke,
        strokeWidth: edge.state && edge.state !== "on_time" ? 2 : 1.5,
        strokeOpacity: look.opacity,
        strokeDasharray: look.dash,
        strokeLinecap: edge.state === "offline" ? "round" : undefined,
      },
      focusable: false,
      zIndex: 2,
      data: { state: edge.state, relation: edge.relation },
    };
  });
  return { nodes, edges: flowEdges };
}
