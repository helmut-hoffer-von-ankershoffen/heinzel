// The team graph's nodes: an artifact or a Heinzelmännchen as a card, a group (a tracker and its rows) as a frame.
// Each card is a link: an artifact opens where it lives, in a new tab; a Heinzelmännchen opens its page, and its
// chat button asks your default Heinzel about it ("Ask <Name>": typed into its composer, never sent). The state is a word and a line style as well as a colour.

import { Handle, type NodeProps, Position } from "@xyflow/react";
import { FileText, LayoutList, type LucideIcon, MessageSquare } from "lucide-react";
import { memo, type ReactNode } from "react";
import { Link } from "react-router";
import { useAskHeinzel } from "@/chat/askHeinzel";
import type { FlowTone } from "@/components/graph/FlowNode";
import { HeinzelFace } from "@/components/HeinzelFace";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { isDurableSession } from "@/lib/chatIntent";
import { cn } from "@/lib/utils";
import { heinzelId, type PromiseState, type TeamNode } from "./jgf";
import type { TeamFlowNode } from "./layout";

const border: Record<FlowTone, string> = {
  neutral: "border-border",
  primary: "border-primary/60",
  ok: "border-ok/60",
  warn: "border-warn/60",
  bad: "border-bad/70",
};
const iconTone: Record<FlowTone, string> = {
  neutral: "bg-muted text-muted-foreground",
  primary: "bg-primary/15 text-primary",
  ok: "bg-ok/15 text-ok",
  warn: "bg-warn/15 text-warn",
  bad: "bg-bad/15 text-bad",
};
const badgeTone: Record<FlowTone, string> = {
  neutral: "border-border text-muted-foreground",
  primary: "border-primary/40 text-primary",
  ok: "border-ok/40 text-ok",
  warn: "border-warn/40 text-warn",
  bad: "border-bad/40 text-bad",
};
/** A fallback is dashed and offline dotted, on the card as on its edges. */
const lineStyle = (state: PromiseState | null) =>
  state === "fell_back" ? "border-dashed" : state === "offline" ? "border-dotted opacity-75" : "border-solid";

export const kindIcon = (node: TeamNode, group = false): LucideIcon =>
  group ? LayoutList : node.kind === "heinzel" ? HeinzelFace : FileText;

const handle = "!size-1.5 !border-0 !bg-border";

/** The card's link: the artifact's own page in a new tab, the personal Heinzel's page, or a team contract. */
function NodeLink({ node, className, children }: { node: TeamNode; className: string; children: ReactNode }) {
  const kind = <span className="sr-only">, {t(`teamGraph.kind.${node.kind}`)}</span>;
  if (node.kind === "heinzel") {
    // A personal Heinzel has its own page; a team Heinzelmännchen is its contract.
    const id = heinzelId(node.id);
    const to = isDurableSession(id) ? `/heinzel/${id}` : `/contracts/${encodeURIComponent(id)}`;
    return (
      <Link to={to} className={cn("nodrag nopan", className)}>
        {children}
        {kind}
      </Link>
    );
  }
  if (node.url) {
    return (
      <a href={node.url} target="_blank" rel="noreferrer noopener" className={cn("nodrag nopan", className)}>
        {children}
        {kind}
        <span className="sr-only"> {t("teamGraph.newTab")}</span>
      </a>
    );
  }
  return (
    <span className={className}>
      {children}
      {kind}
    </span>
  );
}

function StateBadge({ data }: { data: TeamFlowNode["data"] }) {
  if (!data.stateLabel) return null;
  return (
    <span
      className={cn(
        "shrink-0 rounded-full border px-1.5 py-px text-[11px] font-medium",
        badgeTone[data.tone],
        lineStyle(data.node.state),
      )}
    >
      {data.stateLabel}
    </span>
  );
}

function TeamNodeView({ data }: NodeProps<TeamFlowNode>) {
  const { node } = data;
  const Icon = kindIcon(node);
  const ask = useAskHeinzel();
  return (
    <div
      className={cn(
        "group/node relative flex h-full w-full items-center rounded-xl border-2 shadow-sm transition-[box-shadow,border-color]",
        "hover:shadow-md focus-within:ring-2 focus-within:ring-primary/50",
        node.kind === "heinzel" ? "bg-card" : "bg-muted/40",
        border[data.tone],
        lineStyle(node.state),
      )}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} className={handle} />
      <NodeLink
        node={node}
        className="flex h-full min-w-0 flex-1 items-center gap-2.5 rounded-xl px-2.5 text-left outline-none"
      >
        <span
          className={cn("flex size-8 shrink-0 items-center justify-center rounded-lg", iconTone[data.tone])}
          aria-hidden
        >
          <Icon className="size-(--icon)" />
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-ui-sm font-medium text-foreground" title={node.label}>
            {node.label}
          </span>
          <span className="flex min-w-0 items-center gap-1.5">
            <StateBadge data={data} />
            {node.owner && <span className="truncate text-xs text-muted-foreground">{node.owner}</span>}
          </span>
        </span>
      </NodeLink>
      {node.kind === "heinzel" && ask && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => ask.ask(t("teamGraph.askAbout", { label: node.label }))}
          aria-label={t("teamGraph.askHeinzel", { name: ask.name, label: node.label })}
          title={t("teamGraph.askHeinzel", { name: ask.name, label: node.label })}
          className="nodrag nopan mr-1.5 size-7 shrink-0"
        >
          <MessageSquare aria-hidden className="size-4" />
        </Button>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} className={handle} />
    </div>
  );
}

function TeamGroupView({ data }: NodeProps<TeamFlowNode>) {
  const { node } = data;
  const Icon = kindIcon(node, true);
  return (
    <div
      className={cn(
        "h-full w-full rounded-xl border-2 bg-card/40 focus-within:ring-2 focus-within:ring-primary/50",
        border[data.tone],
        lineStyle(node.state),
      )}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} className={handle} />
      <NodeLink node={node} className="flex h-11 items-center gap-2 rounded-t-xl px-3 outline-none">
        <Icon aria-hidden className={cn("size-(--icon) shrink-0", iconTone[data.tone], "bg-transparent")} />
        <span className="min-w-0 flex-1 truncate text-ui-sm font-semibold text-foreground">{node.label}</span>
        <StateBadge data={data} />
      </NodeLink>
      <Handle type="source" position={Position.Right} isConnectable={false} className={handle} />
    </div>
  );
}

export const teamNodeTypes = { team: memo(TeamNodeView), teamGroup: memo(TeamGroupView) };
