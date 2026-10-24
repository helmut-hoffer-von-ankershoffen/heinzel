// The workflow graph placed left to right by dagre: sources, assets, contracts, outputs; styled by kind.
import dagre from "@dagrejs/dagre";
import { type Edge, MarkerType } from "@xyflow/react";
import {
  Bot,
  Box,
  Calendar,
  FileOutput,
  FileText,
  GitPullRequest,
  ListTodo,
  type LucideIcon,
  Mail,
  MessageSquare,
  Plug,
  ScrollText,
  Shapes,
  Sparkles,
  Users,
  Video,
} from "lucide-react";
import { type FlowNodeData, type FlowNodeType, NODE_HEIGHT, NODE_WIDTH } from "@/components/graph/FlowNode";
import { t } from "@/i18n";
import type { WorkflowGraph, WorkflowKind, WorkflowNode } from "./model";

const capabilityIcons: Record<string, LucideIcon> = {
  "code-host": GitPullRequest,
  tracker: ListTodo,
  documents: FileText,
  calendar: Calendar,
  meetings: Video,
  directory: Users,
  chat: MessageSquare,
  mail: Mail,
  "agent-runner": Bot,
};

export const kindIcons: Record<WorkflowKind, LucideIcon> = {
  source: Plug,
  assetType: Shapes,
  asset: Box,
  template: Sparkles,
  contract: ScrollText,
  output: FileOutput,
};

export function iconOf(node: WorkflowNode): LucideIcon {
  return node.kind === "source" ? (capabilityIcons[node.name] ?? Plug) : kindIcons[node.kind];
}

/** How a node looks: its tone, whether it is a suggestion, and the badge that says what state it is in. */
export function visual(node: WorkflowNode): Omit<FlowNodeData, "label" | "sublabel" | "icon"> {
  switch (node.kind) {
    case "source":
      return node.capability?.bound
        ? { tone: "neutral" }
        : { tone: "warn", badge: t("workflow.unbound"), badgeTone: "warn" };
    case "assetType":
      return node.found > 0
        ? { tone: "neutral", badge: t("workflow.foundCount", { count: node.found }), badgeTone: "ok" }
        : { tone: "neutral" };
    case "asset":
      return { tone: "ok", muted: true };
    case "template":
      return { tone: "primary", dashed: true, badge: t("workflow.suggested"), badgeTone: "primary" };
    case "contract":
      return node.errors > 0
        ? { tone: "bad", badge: `${node.errors} ✖`, badgeTone: "bad" }
        : { tone: "primary", badge: node.summary?.scheduled ? "⏱" : undefined };
    case "output":
      if (node.suggested) return { tone: "ok", dashed: true };
      return node.maintained
        ? { tone: "ok" }
        : { tone: "bad", badge: t("workflow.unmaintainedBadge"), badgeTone: "bad" };
  }
}

const RANK: Record<WorkflowKind, number> = { source: 0, asset: 1, assetType: 2, contract: 3, template: 3, output: 4 };

export function layoutWorkflow(
  graph: WorkflowGraph,
  selected: string | null = null,
): { nodes: FlowNodeType[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 20, ranksep: 72, marginx: 8, marginy: 8 });
  g.setDefaultEdgeLabel(() => ({}));
  // Sort so the same answers always give the same picture: sources first, then by kind and name.
  const ordered = [...graph.nodes].sort((a, b) => RANK[a.kind] - RANK[b.kind] || a.id.localeCompare(b.id));
  for (const node of ordered) g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const edge of graph.edges) g.setEdge(edge.source, edge.target);
  dagre.layout(g);
  const near = new Set<string>();
  if (selected) {
    for (const edge of graph.edges) {
      if (edge.source === selected || edge.target === selected) near.add(edge.id);
    }
  }
  const nodes: FlowNodeType[] = ordered.map((node) => {
    const placed = g.node(node.id);
    const look = visual(node);
    return {
      id: node.id,
      type: "hz",
      position: { x: (placed?.x ?? 0) - NODE_WIDTH / 2, y: (placed?.y ?? 0) - NODE_HEIGHT / 2 },
      // Named by its visible text (and its kind for screen readers), not an aria-label that could drift from it.
      data: {
        label: node.label,
        sublabel: node.sublabel,
        icon: iconOf(node),
        kind: t(`workflow.kind.${node.kind}`),
        ...look,
      },
      selected: node.id === selected,
      ariaRole: "button",
      connectable: false,
      draggable: false,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    };
  });
  const edges: Edge[] = graph.edges.map((edge) => {
    const highlighted = near.has(edge.id);
    const colour = highlighted ? "var(--color-primary)" : "var(--color-muted-foreground)";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      label: edge.label,
      labelStyle: { fill: "var(--color-muted-foreground)", fontSize: 10 },
      labelBgStyle: { fill: "var(--color-background)" },
      labelBgPadding: [4, 2],
      markerEnd: { type: MarkerType.ArrowClosed, color: colour, width: 14, height: 14 },
      style: {
        stroke: colour,
        strokeWidth: highlighted ? 2 : 1.25,
        strokeOpacity: highlighted ? 1 : 0.55,
        strokeDasharray: edge.suggested || edge.kind === "finds" ? "5 4" : undefined,
      },
      animated: highlighted && edge.kind === "reads",
      focusable: false,
    };
  });
  return { nodes, edges };
}
