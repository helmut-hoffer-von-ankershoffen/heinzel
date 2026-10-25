// React Flow with the app's look: dotted background, zoom controls and a minimap in the colour tokens, light or dark.
import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  MiniMap,
  type Node,
  type NodeChange,
  type NodeMouseHandler,
  type NodeTypes,
  ReactFlow,
  useReactFlow,
} from "@xyflow/react";
import { useEffect } from "react";
import { type FlowTone, nodeTypes as flowNodeTypes } from "./FlowNode";

const toneColour = {
  neutral: "var(--color-muted-foreground)",
  primary: "var(--color-primary)",
  ok: "var(--color-ok)",
  warn: "var(--color-warn)",
  bad: "var(--color-bad)",
} as const;

const FIT = { padding: 0.15, maxZoom: 1.1 };

/** Fits the view once it mounts; keyed, it fits again when what is drawn, or the room for it, changes. */
function Refit() {
  const { fitView } = useReactFlow();
  useEffect(() => {
    // After React Flow measured the new nodes and its own new size (a resize observer, a frame or two later).
    const timer = window.setTimeout(() => void fitView({ ...FIT, duration: 250 }), 80);
    return () => window.clearTimeout(timer);
  }, [fitView]);
  return null;
}

/** Any node with a tone, which colours it in the minimap. */
type TonedNode = Node<{ tone: FlowTone }>;

export function GraphCanvas<N extends TonedNode>({
  nodes,
  edges,
  label,
  minimapLabel,
  onSelect,
  onClear,
  fitKey,
  nodeTypes = flowNodeTypes as NodeTypes,
}: {
  nodes: N[];
  edges: Edge[];
  label: string;
  minimapLabel: string;
  onSelect?: (id: string) => void;
  onClear?: () => void;
  /** Changes when the view should fit again: the nodes drawn, or the canvas's width. */
  fitKey: string;
  /** The node components; the left-to-right cards (`FlowNode`) unless a screen draws its own. */
  nodeTypes?: NodeTypes;
}) {
  const click: NodeMouseHandler<N> = (_, node) => onSelect?.(node.id);
  // Keyboard selection (Enter on a focused node) arrives as a change, since the nodes are ours to keep.
  const change = (changes: NodeChange<N>[]) => {
    for (const item of changes) if (item.type === "select" && item.selected) onSelect?.(item.id);
  };
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={click}
      onNodesChange={change}
      onPaneClick={onClear}
      fitView
      fitViewOptions={FIT}
      minZoom={0.2}
      maxZoom={2}
      nodesDraggable={false}
      nodesConnectable={false}
      edgesFocusable={false}
      aria-label={label}
      proOptions={{ hideAttribution: true }}
    >
      <Refit key={fitKey} />
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--color-border)" />
      <Controls
        showInteractive={false}
        className="!overflow-hidden !rounded-lg !border !border-border !shadow-sm [&_button]:!border-border [&_button:hover]:!bg-accent [&_svg]:!fill-current"
      />
      <MiniMap
        pannable
        zoomable
        ariaLabel={minimapLabel}
        bgColor="var(--color-card)"
        maskColor="color-mix(in oklab, var(--color-muted) 70%, transparent)"
        maskStrokeColor="var(--color-border)"
        nodeColor={(node) => toneColour[(node as TonedNode).data.tone]}
        nodeStrokeColor="transparent"
        nodeBorderRadius={6}
        style={{ width: 168, height: 112 }}
        className="!overflow-hidden !rounded-lg !border !border-border !shadow-sm"
      />
    </ReactFlow>
  );
}
