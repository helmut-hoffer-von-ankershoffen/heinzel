// The team graph placed left to right by elk's layered algorithm, groups laid out around their members with the
// edges into them (ADR 0053). The bundled build runs elk on this thread, so no worker file is needed under the CSP
// (`worker-src 'self'`); it is loaded when a graph is first laid out, in a chunk of its own.
import type { ELK, ElkNode } from "elkjs/lib/elk.bundled.js";
import type { TeamNode } from "./jgf";
import { GROUP_HEADER, GROUP_PADDING, NODE_HEIGHT, NODE_WIDTH, type Place, type Placed } from "./placement";

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
const GROUP_OPTIONS = {
  "elk.padding": `[top=${GROUP_HEADER},left=${GROUP_PADDING},bottom=${GROUP_PADDING},right=${GROUP_PADDING}]`,
};

const toPlaced = (node: ElkNode): Placed => ({
  id: node.id,
  x: node.x ?? 0,
  y: node.y ?? 0,
  width: node.width ?? NODE_WIDTH,
  height: node.height ?? NODE_HEIGHT,
  ...(node.children?.length ? { children: node.children.map(toPlaced) } : {}),
});

export const placeTeam: Place = async (graph) => {
  // Sorted, so the same graph always gives the same picture.
  const ordered = [...graph.nodes].sort((a, b) => a.id.localeCompare(b.id));
  const elkNode = (node: TeamNode): ElkNode => {
    const inside = ordered.filter((child) => child.parent === node.id);
    return inside.length > 0
      ? { id: node.id, layoutOptions: GROUP_OPTIONS, children: inside.map(elkNode) }
      : { id: node.id, width: NODE_WIDTH, height: NODE_HEIGHT };
  };
  const elk = await loadElk();
  const placed = await elk.layout({
    id: "root",
    layoutOptions: ROOT_OPTIONS,
    children: ordered.filter((node) => !node.parent).map(elkNode),
    edges: graph.edges.map((edge) => ({ id: edge.id, sources: [edge.from], targets: [edge.to] })),
  });
  return (placed.children ?? []).map(toPlaced);
};
