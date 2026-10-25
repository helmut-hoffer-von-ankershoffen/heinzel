// `hz heinzel show NAME`: one contract as the daemon runs it, and what to do next.
import { useParams } from "react-router";
import { useContract } from "@/api/queries";
import { CopyCommand, Loaded, Page } from "@/components/common";
import { Badge, Card, CardTitle } from "@/components/ui/card";
import { t } from "@/i18n";

export function Contract() {
  const name = decodeURIComponent(useParams().name ?? "");
  const contract = useContract(name);
  return (
    <Loaded query={contract}>
      {(shown) => (
        <Page title={shown.title ?? shown.name} intro={shown.description ?? undefined}>
          <div className="flex flex-wrap gap-2">
            <Badge tone={shown.ready ? "ok" : "warn"}>
              {shown.ready ? t("contract.ready") : t("contract.notReady")}
            </Badge>
            <Badge>
              {t("contract.schedule")}: {shown.schedule ?? t("contract.byHand")}
            </Badge>
            <Badge>{t("contract.drafts", { count: shown.draft_count })}</Badge>
            <code className="font-mono text-xs text-muted-foreground">{shown.file}</code>
          </div>
          {shown.why_not_scheduled && <p className="text-sm text-muted-foreground">{shown.why_not_scheduled}</p>}
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardTitle>{t("contract.requirements")}</CardTitle>
              <ul className="flex flex-col gap-1 text-sm">
                {shown.requirements.map((requirement) => (
                  <li key={requirement.requirement}>
                    <code className="font-mono text-xs">{requirement.requirement}</code>{" "}
                    {requirement.adapter ? <Badge tone="ok">{requirement.adapter}</Badge> : <Badge tone="bad">✖</Badge>}
                    {requirement.problem && <p className="text-xs text-bad">{requirement.problem}</p>}
                    {requirement.fix && <p className="text-xs text-muted-foreground">{requirement.fix}</p>}
                  </li>
                ))}
              </ul>
            </Card>
            <Card>
              <CardTitle>{t("contract.inputs")}</CardTitle>
              <ul className="text-sm">
                {Object.entries(shown.inputs).map(([input, read]) => (
                  <li key={input}>
                    {input}: <code className="font-mono text-xs">{read}</code>
                  </li>
                ))}
              </ul>
              <CardTitle className="mt-3">{t("contract.outputs")}</CardTitle>
              <ul className="text-sm">
                {Object.entries(shown.outputs).map(([output, kind]) => (
                  <li key={output}>
                    {output}: {kind}
                  </li>
                ))}
              </ul>
            </Card>
          </div>
          {shown.diagnostics.length > 0 && (
            <Card>
              <CardTitle>{t("contract.diagnostics")}</CardTitle>
              <ul className="text-sm">
                {shown.diagnostics.map((diagnostic) => (
                  <li key={`${diagnostic.code}-${diagnostic.line}-${diagnostic.field}`}>
                    <Badge tone={diagnostic.severity === "error" ? "bad" : "warn"}>{diagnostic.code}</Badge> line{" "}
                    {diagnostic.line}: {diagnostic.message} {diagnostic.hint}
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <Card>
            <CardTitle>{t("contract.next")}</CardTitle>
            <ul className="flex flex-col gap-2 text-sm">
              {shown.next.map((step) => (
                <li key={step.command} className="flex flex-col items-start gap-1">
                  <CopyCommand command={step.command} />
                  <span className="text-xs text-muted-foreground">{step.why}</span>
                </li>
              ))}
            </ul>
          </Card>
        </Page>
      )}
    </Loaded>
  );
}
