// The Team graph screen (`/graph`): which Heinzelmännchen read and write which artifacts, and whether each promise is
// kept. The header says how the flow is doing ("Weekly report waiting on 1 update"), never how a person is doing.
// The graph comes from `hz graph` as JSON Graph Format; elk lays it out, React Flow draws it, Mermaid is an export.
import { useQuery } from "@tanstack/react-query";
import type { Edge } from "@xyflow/react";
import { Check, Copy } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { problem } from "@/api/client";
import { useInfo } from "@/api/queries";
import { token } from "@/api/token";
import { Empty, Loaded } from "@/components/common";
import { GraphCanvas } from "@/components/graph/GraphCanvas";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { fromJgf, pending, type TeamGraph, toMermaid } from "./jgf";
import { TeamLegend } from "./Legend";
import { layoutTeam, type TeamFlowNode } from "./layout";
import { teamNodeTypes } from "./TeamNodes";

/** `GET /api/graph` (`hz graph`); null when there is none (404), so the screen says how to make one. */
export function useTeamGraph() {
  // Without the graph module there is nothing to ask, and asking would log a 404.
  const info = useInfo();
  const absent = info.data !== undefined && !info.data.modules.some((module) => module.qualname === "graph");
  return useQuery({
    enabled: info.data !== undefined || info.isError,
    queryKey: ["graph", "graph", absent],
    queryFn: async (): Promise<TeamGraph | null> => {
      if (absent) return null;
      const request = new Request(new URL("/api/graph", window.location.origin), {
        headers: { Authorization: `Bearer ${token() ?? ""}` },
      });
      const response = await fetch(request);
      if (response.status === 404) return null;
      const body: unknown = await response.json().catch(() => null);
      if (!response.ok) throw problem(response.status, body);
      return fromJgf(body);
    },
  });
}

/** The header line: how the flow is doing as a whole. */
export function summarise(graph: TeamGraph): { text: string; tone: "ok" | "warn" | "bad" } {
  const { waiting, late } = pending(graph);
  const tone = late > 0 ? "bad" : waiting > 0 ? "warn" : "ok";
  if (graph.summary) return { text: graph.summary, tone };
  const flow = graph.label ?? t("teamGraph.summary.flow");
  if (waiting === 0) return { text: t("teamGraph.summary.kept", { flow }), tone };
  const text = t(waiting === 1 ? "teamGraph.summary.waitingOne" : "teamGraph.summary.waitingMany", {
    flow,
    count: waiting,
  });
  return { text: late > 0 ? `${text} · ${t("teamGraph.summary.late", { count: late })}` : text, tone };
}

function CopyMermaid({ graph }: { graph: TeamGraph }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(toMermaid(graph));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Button variant="outline" onClick={copy}>
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
      {t("teamGraph.copyMermaid")}
      <span className="sr-only" aria-live="polite">
        {copied ? t("teamGraph.copied") : ""}
      </span>
    </Button>
  );
}

/** What the canvas shows, as a list for screen readers: each promise, who reads or writes what, and its state. */
function Promises({ graph }: { graph: TeamGraph }) {
  const label = new Map(graph.nodes.map((node) => [node.id, node.label]));
  return (
    <ul className="sr-only" aria-label={t("teamGraph.promises")}>
      {graph.edges.map((edge) => {
        const [actor, object] = edge.relation === "reads" ? [edge.to, edge.from] : [edge.from, edge.to];
        return (
          <li key={edge.id}>
            {t("teamGraph.promise", {
              actor: label.get(actor) ?? actor,
              relation: t(`teamGraph.relation.${edge.relation}`),
              object: label.get(object) ?? object,
              state: edge.state ? t(`teamGraph.state.${edge.state}`) : t("teamGraph.state.unknown"),
            })}
          </li>
        );
      })}
    </ul>
  );
}

function Canvas({ graph }: { graph: TeamGraph }) {
  const [placed, setPlaced] = useState<{ nodes: TeamFlowNode[]; edges: Edge[] } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    layoutTeam(graph).then(
      (result) => current && setPlaced(result),
      (reason: unknown) => current && setFailed(reason instanceof Error ? reason.message : String(reason)),
    );
    return () => {
      current = false;
    };
  }, [graph]);
  if (failed) {
    return (
      <p role="alert" className="rounded-md border border-bad/40 p-3 text-sm text-bad">
        {t("state.error")} {failed}
      </p>
    );
  }
  return (
    <section
      aria-label={t("teamGraph.graph")}
      className="h-[34rem] min-w-0 overflow-hidden rounded-xl border border-border bg-background"
    >
      {placed ? (
        <GraphCanvas
          nodes={placed.nodes}
          edges={placed.edges}
          nodeTypes={teamNodeTypes}
          label={t("teamGraph.graph")}
          minimapLabel={t("teamGraph.minimap")}
          fitKey={`${placed.nodes.length}/${placed.edges.length}`}
        />
      ) : (
        <p role="status" className="p-4 text-sm text-muted-foreground">
          {t("state.loading")}
        </p>
      )}
    </section>
  );
}

const summaryTone = { ok: "text-ok", warn: "text-warn", bad: "text-bad" } as const;

function TeamGraphBody({ graph }: { graph: TeamGraph }) {
  const summary = useMemo(() => summarise(graph), [graph]);
  return (
    <>
      <header className="flex flex-wrap items-center justify-between gap-3">
        <p className={cn("text-ui font-medium", summaryTone[summary.tone])} aria-live="polite">
          {summary.text}
        </p>
        <CopyMermaid graph={graph} />
      </header>
      <Canvas graph={graph} />
      <Promises graph={graph} />
      <TeamLegend />
    </>
  );
}

export function GraphView() {
  const query = useTeamGraph();
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-8 pt-4 pb-16">
      <h1 className="sr-only">{t("nav.teamGraph")}</h1>
      <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">{t("teamGraph.intro")}</p>
      <Loaded query={query}>
        {(graph) =>
          graph === null || graph.nodes.length === 0 ? (
            <Empty text={t("teamGraph.empty")} commands={["hz graph"]} gnome={{ age: "baby" }} />
          ) : (
            <TeamGraphBody graph={graph} />
          )
        }
      </Loaded>
    </div>
  );
}
