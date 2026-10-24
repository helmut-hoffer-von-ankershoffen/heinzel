// The side panel for the selected node: what it is, how it connects, and the hz commands for it.
import { ExternalLink, X } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { CopyCommand } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/card";
import { t } from "@/i18n";
import { contractPath } from "@/layout/navigation";
import { iconOf } from "./layout";
import { shellWord, type WorkflowGraph, type WorkflowNode } from "./model";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{label}</span>
      <div className="text-sm break-words">{children}</div>
    </div>
  );
}

/** The commands that matter for `node`, the most useful first. */
export function commandsFor(node: WorkflowNode, graph: WorkflowGraph): string[] {
  switch (node.kind) {
    case "source":
      return node.capability?.bound
        ? ["hz system capability list"]
        : [`hz system plugin search --text ${shellWord(node.name)}`, "hz system capability list"];
    case "assetType":
      return ["hz workflow discover", "hz workflow playbooks"];
    case "asset": {
      const templates = graph.nodes.filter(
        (other) => other.kind === "template" && other.template.assets.includes(node.candidate.asset),
      );
      return templates.length > 0
        ? templates.map((other) =>
            other.kind === "template"
              ? `${other.template.command} --asset ${shellWord(`${node.candidate.asset}=${node.candidate.ref}`)}`
              : "",
          )
        : ["hz workflow discover"];
    }
    case "template":
      return [node.command, "hz workflow playbooks"];
    case "contract":
      return [
        `hz workflow show ${node.qualified}`,
        `hz workflow run ${node.qualified} --dry-run`,
        `hz workflow check --path ${shellWord(node.summary?.file ?? node.qualified)}`,
      ].slice(0, node.summary ? 3 : 2);
    case "output":
      if (node.suggested) return [];
      if (!node.maintained) return ["hz workflow graph", "hz workflow new <name>"];
      return node.contract
        ? [`hz workflow drafts --name ${node.contract.split(":").at(-1)} --latest`, "hz workflow graph"]
        : ["hz workflow graph"];
  }
}

function Details({ node }: { node: WorkflowNode }) {
  switch (node.kind) {
    case "source":
      return (
        <>
          {node.capability?.summary && <p className="text-sm text-muted-foreground">{node.capability.summary}</p>}
          <Field label={t("workflow.panel.adapter")}>
            {node.capability?.bound ? (
              <Badge tone="ok">{node.capability.bound}</Badge>
            ) : (
              <Badge tone="warn">{t("workflow.unbound")}</Badge>
            )}
          </Field>
          {node.reads.length > 0 && (
            <Field label={t("workflow.panel.reads")}>
              <ul className="flex flex-col gap-0.5">
                {node.reads.map((read) => (
                  <li key={read}>
                    <code className="font-mono text-xs">{read}</code>
                  </li>
                ))}
              </ul>
            </Field>
          )}
        </>
      );
    case "assetType":
      return (
        <>
          <p className="text-sm text-muted-foreground">{node.asset.description}</p>
          <Field label={t("workflow.panel.playbook")}>{node.playbook.module}</Field>
          {node.asset.kept_by && <Field label={t("workflow.panel.keptBy")}>{node.asset.kept_by}</Field>}
          {node.asset.hints.length > 0 && (
            <Field label={t("workflow.panel.hints")}>
              <ul className="flex flex-col gap-1">
                {node.asset.hints.map((hint) => (
                  <li key={`${hint.read}-${hint.summary}`}>
                    {hint.summary} <code className="font-mono text-xs text-muted-foreground">{hint.read}</code>
                  </li>
                ))}
              </ul>
            </Field>
          )}
          <Field label={t("workflow.panel.found")}>{t("workflow.foundCount", { count: node.found })}</Field>
        </>
      );
    case "asset":
      return (
        <>
          <Field label={t("workflow.panel.type")}>{node.candidate.asset}</Field>
          <Field label={t("workflow.panel.ref")}>
            <code className="font-mono text-xs">{node.candidate.ref}</code>
          </Field>
          <Field label={t("workflow.panel.foundBy")}>
            <code className="font-mono text-xs">{node.candidate.hint}</code>
          </Field>
          {node.candidate.url && (
            <a
              href={node.candidate.url}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-1 text-sm text-primary underline-offset-2 hover:underline"
            >
              {t("workflow.panel.open")} <ExternalLink aria-hidden className="size-3.5" />
            </a>
          )}
        </>
      );
    case "template":
      return (
        <>
          <p className="text-sm text-muted-foreground">{node.playbook.summary}</p>
          <Field label={t("workflow.panel.template")}>
            <code className="font-mono text-xs">{node.template.template}</code>
          </Field>
          {node.template.assets.length > 0 && (
            <Field label={t("workflow.panel.readsAssets")}>{node.template.assets.join(", ")}</Field>
          )}
          {node.template.keeps.length > 0 && (
            <Field label={t("workflow.panel.keeps")}>{node.template.keeps.join(", ")}</Field>
          )}
          <p className="text-xs text-muted-foreground">{t("workflow.panel.templateHow")}</p>
        </>
      );
    case "contract":
      return (
        <>
          <Field label={t("workflow.panel.name")}>
            <code className="font-mono text-xs">{node.qualified}</code>
          </Field>
          <Field label={t("contract.schedule")}>{node.summary?.schedule ?? t("contract.byHand")}</Field>
          {node.errors > 0 && (
            <Field label={t("contract.diagnostics")}>
              <Badge tone="bad">{node.errors} ✖</Badge>
            </Field>
          )}
          <Link
            to={contractPath(node.qualified)}
            className="text-sm font-medium text-primary underline-offset-2 hover:underline"
          >
            {t("workflow.panel.openContract")}
          </Link>
        </>
      );
    case "output":
      return (
        <>
          {node.suggested ? (
            <p className="text-sm text-muted-foreground">{t("workflow.panel.suggestedOutput")}</p>
          ) : (
            <Field label={t("workflow.panel.ref")}>
              <code className="font-mono text-xs">{node.ref}</code>
            </Field>
          )}
          {!node.maintained && <p className="text-sm text-bad">{t("workflow.panel.unmaintained")}</p>}
        </>
      );
  }
}

export function NodePanel({ node, graph, onClose }: { node: WorkflowNode; graph: WorkflowGraph; onClose: () => void }) {
  const Icon = iconOf(node);
  const commands = commandsFor(node, graph);
  const inbound = graph.edges.filter((edge) => edge.target === node.id).length;
  const outbound = graph.edges.filter((edge) => edge.source === node.id).length;
  return (
    <aside
      aria-labelledby="workflow-panel-title"
      className="flex w-full shrink-0 flex-col gap-4 rounded-xl border border-border bg-card p-4 shadow-sm lg:w-80"
    >
      <header className="flex items-start gap-3">
        <span
          className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary"
          aria-hidden
        >
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            {t(`workflow.kind.${node.kind}`)}
          </p>
          <h2 id="workflow-panel-title" className="truncate font-semibold">
            {node.label}
          </h2>
        </div>
        <Button variant="ghost" size="icon" className="size-7" onClick={onClose} aria-label={t("workflow.panel.close")}>
          <X aria-hidden />
        </Button>
      </header>
      <div className="flex flex-col gap-3">
        <Details node={node} />
        <Field label={t("workflow.panel.connections")}>
          {t("workflow.panel.connectionCount", { inbound, outbound })}
        </Field>
      </div>
      {commands.length > 0 && (
        <section aria-labelledby="workflow-panel-commands" className="flex flex-col gap-1.5">
          <h3
            id="workflow-panel-commands"
            className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase"
          >
            {t("workflow.panel.commands")}
          </h3>
          {commands.map((command) => (
            <CopyCommand key={command} command={command} />
          ))}
        </section>
      )}
    </aside>
  );
}
