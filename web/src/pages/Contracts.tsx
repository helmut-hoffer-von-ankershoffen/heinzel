// `hz workflow graph`: contracts, the assets they maintain and what they read, laid out as a DAG with dagre.
import dagre from "@dagrejs/dagre";
import { Background, Controls, type Edge, MarkerType, type Node, ReactFlow } from "@xyflow/react";
import { useMemo } from "react";
import { Link, useNavigate } from "react-router";
import type { SchemaContractGraph } from "@/api/client";
import { useContracts, useGraph } from "@/api/queries";
import { Empty, Loaded, Page } from "@/components/common";
import { Badge, Card, CardTitle } from "@/components/ui/card";
import { t } from "@/i18n";
import { contractPath } from "@/layout/navigation";
import { cn } from "@/lib/utils";

const WIDTH = 200;
const HEIGHT = 48;
const kindStyle = {
  contract: "border-primary bg-card font-semibold",
  asset: "border-ok/60 bg-card",
  source: "border-dashed border-border bg-muted text-muted-foreground",
};

function label(id: string, kind: string): string {
  if (kind === "asset") return id.split("#").at(-1) ?? id;
  if (kind === "contract") return id.split(":").at(-1) ?? id;
  return id;
}

/** Nodes placed left to right by dagre: sources, then the contracts reading them, then the assets they maintain. */
export function layout(graph: SchemaContractGraph): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 24, ranksep: 64 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const node of graph.nodes) g.setNode(node.id, { width: WIDTH, height: HEIGHT });
  for (const edge of graph.edges) g.setEdge(edge.source, edge.target);
  dagre.layout(g);
  const nodes: Node[] = graph.nodes.map((node) => {
    const placed = g.node(node.id);
    return {
      id: node.id,
      position: { x: (placed?.x ?? 0) - WIDTH / 2, y: (placed?.y ?? 0) - HEIGHT / 2 },
      data: { label: label(node.id, node.kind) },
      className: cn(
        "rounded-md border-2 px-2 py-1 text-xs",
        kindStyle[node.kind],
        node.kind === "asset" && !node.maintained && "border-bad",
      ),
      ariaLabel: `${t(`contracts.kind.${node.kind}`)} ${node.id}`,
      style: { width: WIDTH },
      connectable: false,
    };
  });
  const edges: Edge[] = graph.edges.map((edge) => ({
    id: `${edge.source}->${edge.target}`,
    source: edge.source,
    target: edge.target,
    label: edge.input ?? undefined,
    markerEnd: { type: MarkerType.ArrowClosed },
    animated: edge.kind === "reads",
  }));
  return { nodes, edges };
}

export function Contracts() {
  const graph = useGraph();
  const contracts = useContracts();
  const navigate = useNavigate();
  const placed = useMemo(() => (graph.data ? layout(graph.data) : null), [graph.data]);
  return (
    <Page title={t("contracts.title")} intro={t("contracts.intro")}>
      <Loaded query={graph}>
        {(data) =>
          data.nodes.length === 0 ? (
            <Empty text={t("contracts.empty")} commands={["hz workflow new weekly-digest", "hz workflow playbooks"]} />
          ) : (
            <>
              <section className="h-[28rem] rounded-lg border border-border" aria-label={t("contracts.title")}>
                {placed && (
                  <ReactFlow
                    nodes={placed.nodes}
                    edges={placed.edges}
                    fitView
                    nodesDraggable={false}
                    nodesConnectable={false}
                    onNodeClick={(_, node) => {
                      if (data.nodes.find((candidate) => candidate.id === node.id)?.kind === "contract") {
                        navigate(contractPath(node.id));
                      }
                    }}
                    proOptions={{ hideAttribution: true }}
                  >
                    <Background />
                    <Controls showInteractive={false} />
                  </ReactFlow>
                )}
              </section>
              {data.cycles.length > 0 && (
                <Card>
                  <CardTitle>{t("contracts.cycles")}</CardTitle>
                  <ul className="text-sm">
                    {data.cycles.map((cycle) => (
                      <li key={cycle.join(">")}>{cycle.join(" → ")}</li>
                    ))}
                  </ul>
                </Card>
              )}
              {data.unmaintained.length > 0 && (
                <Card>
                  <CardTitle>{t("contracts.unmaintained")}</CardTitle>
                  <ul className="text-sm">
                    {data.unmaintained.map((item) => (
                      <li key={`${item.contract}/${item.input}`}>
                        <code className="font-mono text-xs">{item.contract}</code> · {item.input}:{" "}
                        <code className="font-mono text-xs">{item.asset}</code> — {item.why}
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
            </>
          )
        }
      </Loaded>
      {contracts.data && contracts.data.contracts.length > 0 && (
        <Card>
          <CardTitle>{t("contracts.list")}</CardTitle>
          <ul className="flex flex-col gap-1 text-sm">
            {contracts.data.contracts.map((contract) => {
              const qualified = `${contract.namespace}:${contract.name}`;
              return (
                <li key={qualified} className="flex flex-wrap items-center gap-2">
                  <Link
                    to={contractPath(qualified)}
                    className="font-medium text-primary underline-offset-2 hover:underline"
                  >
                    {contract.title ?? contract.name}
                  </Link>
                  <code className="font-mono text-xs text-muted-foreground">{qualified}</code>
                  <Badge>{contract.schedule ?? t("contract.byHand")}</Badge>
                  {contract.errors > 0 && <Badge tone="bad">{contract.errors} ✖</Badge>}
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </Page>
  );
}
