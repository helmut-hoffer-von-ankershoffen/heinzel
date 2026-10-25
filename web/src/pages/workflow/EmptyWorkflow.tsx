// Before there is anything to draw: the shape a workflow will take, and the commands that start one.
import { ArrowRight, FileOutput, Plug, ScrollText } from "lucide-react";
import { CopyCommand } from "@/components/common";
import { t } from "@/i18n";

const stages = [
  { icon: Plug, title: "workflow.empty.sources", text: "workflow.empty.sourcesText" },
  { icon: ScrollText, title: "workflow.empty.contracts", text: "workflow.empty.contractsText" },
  { icon: FileOutput, title: "workflow.empty.outputs", text: "workflow.empty.outputsText" },
] as const;

export function EmptyWorkflow({ hasSources }: { hasSources: boolean }) {
  const commands = [
    "hz heinzel new weekly-digest",
    "hz system plugin search --text playbook",
    ...(hasSources ? [] : ["hz system capability list"]),
  ];
  return (
    <section
      aria-labelledby="workflow-empty-title"
      className="flex flex-col gap-5 rounded-xl border border-dashed border-border bg-card/40 p-6"
    >
      <ol className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center" aria-hidden>
        {stages.map((stage, index) => {
          const Icon = stage.icon;
          return (
            <li key={stage.title} className="flex flex-1 items-center gap-2">
              <div className="flex flex-1 items-center gap-2.5 rounded-xl border border-dashed border-border bg-card px-3 py-2.5">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                  <Icon className="size-4" />
                </span>
                <span className="flex flex-col">
                  <span className="text-xs font-medium">{t(stage.title)}</span>
                  <span className="text-[11px] text-muted-foreground">{t(stage.text)}</span>
                </span>
              </div>
              {index < stages.length - 1 && <ArrowRight className="hidden size-4 text-muted-foreground sm:block" />}
            </li>
          );
        })}
      </ol>
      <div className="flex flex-col items-start gap-2">
        <h2 id="workflow-empty-title" className="font-semibold">
          {t("workflow.empty.title")}
        </h2>
        <p className="max-w-2xl text-sm text-muted-foreground">{t("workflow.empty.text")}</p>
        {commands.map((command) => (
          <CopyCommand key={command} command={command} />
        ))}
      </div>
    </section>
  );
}
