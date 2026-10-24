// The team graph, placed by the layout engine (`place-elk.ts`, ADR 0053), handed to React Flow: group nodes first
// with their size, the nodes in them positioned relative to them; edges coloured and dashed by promise state.
import { type Edge, MarkerType, type Node } from "@xyflow/react";
import type { FlowTone } from "@/components/graph/FlowNode";
import { t } from "@/i18n";
import { groupIds, type PromiseState, type TeamGraph, type TeamNode } from "./jgf";
import { placeTeam } from "./place-elk";
import type { Placed } from "./placement";

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

export async function layoutTeam(graph: TeamGraph): Promise<{ nodes: TeamFlowNode[]; edges: Edge[] }> {
  const groups = groupIds(graph);
  const placed = await placeTeam(graph);
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const nodes: TeamFlowNode[] = [];
  // Parents before their children, as React Flow wants them; positions are relative to the parent.
  const visit = (children: Placed[] | undefined, parent: string | undefined) => {
    for (const child of children ?? []) {
      const node = byId.get(child.id);
      if (!node) continue;
      const group = groups.has(node.id);
      nodes.push({
        id: node.id,
        type: group ? "teamGroup" : "team",
        position: { x: child.x, y: child.y },
        data: {
          node,
          tone: node.state ? stateTone[node.state] : "neutral",
          stateLabel: node.state ? t(`teamGraph.state.${node.state}`) : undefined,
        },
        ...(parent ? { parentId: parent, extent: "parent" as const } : {}),
        width: child.width,
        height: child.height,
        style: { width: child.width, height: child.height },
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
  visit(placed, undefined);

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
