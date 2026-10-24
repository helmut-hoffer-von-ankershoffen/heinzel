// The Workflow screen (`/contracts`): everything hz knows of your workflow as one graph, left to right. The systems
// you connected, the asset types playbooks keep and what discovery found of them, your contracts and the templates
// playbooks suggest, and the outputs they maintain. Select a node for its details and its hz command.
import { useQuery } from "@tanstack/react-query";
import { Radar } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api, unwrap } from "@/api/client";
import { useContracts, useDiscover, useGraph, usePlaybooks } from "@/api/queries";
import { Loaded } from "@/components/common";
import { GraphCanvas } from "@/components/graph/GraphCanvas";
import { Legend, type LegendItem } from "@/components/graph/Legend";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardTitle } from "@/components/ui/card";
import { t } from "@/i18n";
import { contractPath } from "@/layout/navigation";
import { EmptyWorkflow } from "./workflow/EmptyWorkflow";
import { kindIcons, layoutWorkflow } from "./workflow/layout";
import { buildWorkflow, isEmpty } from "./workflow/model";
import { NodePanel } from "./workflow/NodePanel";

/** `hz system capability list`: the systems you connected are the graph's sources, even before anything reads them. */
const useCapabilities = () =>
  useQuery({
    queryKey: ["system", "capability", "list"],
    queryFn: () => unwrap(api.GET("/system/capability/list")),
    retry: false,
  });

const legend: LegendItem[] = [
  { label: t("workflow.kind.source"), icon: kindIcons.source, className: "border-border bg-card" },
  { label: t("workflow.kind.assetType"), icon: kindIcons.assetType, className: "border-border bg-card" },
  { label: t("workflow.kind.asset"), icon: kindIcons.asset, className: "border-ok/60 bg-muted/50 text-ok" },
  { label: t("workflow.kind.contract"), icon: kindIcons.contract, className: "border-primary/60 bg-card text-primary" },
  {
    label: t("workflow.kind.template"),
    icon: kindIcons.template,
    className: "border-dashed border-primary/60 bg-primary/5 text-primary",
  },
  { label: t("workflow.kind.output"), icon: kindIcons.output, className: "border-ok/60 bg-card text-ok" },
];

export function Contracts() {
  const graph = useGraph();
  const contracts = useContracts();
  const playbooks = usePlaybooks();
  const capabilities = useCapabilities();
  const [asked, setAsked] = useState(false);
  const discovery = useDiscover(asked);
  const [selected, setSelected] = useState<string | null>(null);

  const workflow = useMemo(
    () =>
      buildWorkflow({
        graph: graph.data,
        contracts: contracts.data?.contracts,
        playbooks: playbooks.data?.playbooks,
        capabilities: capabilities.data?.capabilities,
        discovery: discovery.data,
      }),
    [graph.data, contracts.data, playbooks.data, capabilities.data, discovery.data],
  );
  const placed = useMemo(() => layoutWorkflow(workflow, selected), [workflow, selected]);
  const node = workflow.nodes.find((candidate) => candidate.id === selected) ?? null;
  const suggestions = workflow.nodes.filter((candidate) => candidate.kind === "template").length;
  const discover = () => (asked ? void discovery.refetch() : setAsked(true));

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-8 pt-4 pb-16">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="sr-only">{t("contracts.title")}</h1>
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">{t("workflow.intro")}</p>
        </div>
        <Button variant="outline" onClick={discover} disabled={discovery.isFetching}>
          <Radar aria-hidden />
          {discovery.isFetching ? t("workflow.discovering") : t("workflow.discover")}
        </Button>
      </header>
      {asked && (
        <div aria-live="polite" className="flex flex-col gap-1 text-sm">
          {discovery.error && (
            <p role="alert" className="rounded-md border border-warn/40 px-3 py-2 text-warn">
              {t("workflow.discoverFailed", { problem: discovery.error.message })}
            </p>
          )}
          {discovery.data && (
            <p className="text-muted-foreground">
              {t("workflow.discovered", { count: discovery.data.candidates.length })}
            </p>
          )}
          {discovery.data?.problems.map((problem) => (
            <p key={`${problem.playbook}/${problem.asset}/${problem.hint}`} className="text-xs text-warn">
              {problem.asset} ({problem.hint}): {problem.problem}
            </p>
          ))}
        </div>
      )}
      <Loaded query={graph}>
        {(data) => (
          <>
            {isEmpty(workflow) && <EmptyWorkflow hasSources={workflow.nodes.length > 0} />}
            {!isEmpty(workflow) && suggestions > 0 && (contracts.data?.contracts.length ?? 0) === 0 && (
              <p className="text-sm text-muted-foreground">{t("workflow.suggestionsHint", { count: suggestions })}</p>
            )}
            {workflow.nodes.length > 0 && (
              <div className="flex flex-col gap-4 lg:flex-row">
                <section
                  aria-label={t("workflow.graph")}
                  className="h-[34rem] min-w-0 flex-1 overflow-hidden rounded-xl border border-border bg-background"
                >
                  <GraphCanvas
                    nodes={placed.nodes}
                    edges={placed.edges}
                    label={t("workflow.graph")}
                    minimapLabel={t("workflow.minimap")}
                    onSelect={setSelected}
                    onClear={() => setSelected(null)}
                    fitKey={`${workflow.nodes.length}/${node ? "panel" : "full"}`}
                  />
                </section>
                {node && <NodePanel node={node} graph={workflow} onClose={() => setSelected(null)} />}
              </div>
            )}
            {workflow.nodes.length > 0 && <Legend title={t("workflow.legend")} items={legend} />}
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
        )}
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
    </div>
  );
}
