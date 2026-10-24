// A node of a left-to-right graph: a rounded card with an icon, a title, a subtitle and a badge, in the app's tokens.
import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import type { LucideIcon } from "lucide-react";
import { memo } from "react";
import { cn } from "@/lib/utils";

export type FlowTone = "neutral" | "primary" | "ok" | "warn" | "bad";

export type FlowNodeData = {
  label: string;
  sublabel?: string;
  /** What kind of node it is, read after its visible text by screen readers. */
  kind?: string;
  icon: LucideIcon;
  tone: FlowTone;
  /** Dashed: a suggestion, not something that exists yet. */
  dashed?: boolean;
  /** Muted: found or implied, less prominent than what you made. */
  muted?: boolean;
  badge?: string;
  badgeTone?: FlowTone;
};

export type FlowNodeType = Node<FlowNodeData, "hz">;

export const NODE_WIDTH = 232;
export const NODE_HEIGHT = 58;

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

function FlowNodeView({ data, selected }: NodeProps<FlowNodeType>) {
  const Icon = data.icon;
  return (
    <div
      className={cn(
        "flex items-center gap-2.5 rounded-xl border px-2.5 text-left shadow-sm transition-[box-shadow,border-color]",
        "hover:border-primary/60 hover:shadow-md",
        data.muted ? "bg-muted/50" : "bg-card",
        data.dashed && "border-dashed bg-primary/[0.04] shadow-none",
        border[data.tone],
        selected && "border-primary ring-2 ring-primary/40",
      )}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} className="!size-1.5 !border-0 !bg-border" />
      <span
        className={cn("flex size-8 shrink-0 items-center justify-center rounded-lg", iconTone[data.tone])}
        aria-hidden
      >
        <Icon className="size-4" />
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-xs font-medium text-foreground">{data.label}</span>
        {data.sublabel && <span className="truncate text-[11px] text-muted-foreground">{data.sublabel}</span>}
      </span>
      {data.badge && (
        <span
          className={cn(
            "shrink-0 rounded-full border px-1.5 py-px text-[10px] font-medium",
            badgeTone[data.badgeTone ?? "neutral"],
          )}
        >
          {data.badge}
        </span>
      )}
      {data.kind && <span className="sr-only">, {data.kind}</span>}
      <Handle
        type="source"
        position={Position.Right}
        isConnectable={false}
        className="!size-1.5 !border-0 !bg-border"
      />
    </div>
  );
}

export const FlowNode = memo(FlowNodeView);
export const nodeTypes = { hz: FlowNode };
