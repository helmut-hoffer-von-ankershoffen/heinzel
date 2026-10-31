// `hz heinzel list`: your Heinzelmännchen, one per contract you have, with their state and their last and next run.
import { Link } from "react-router";
import type { SchemaHeinzelSummary } from "@/api/client";
import { useHeinzelmaennchen } from "@/api/queries";
import { CopyCommand, Empty, Loaded, Page } from "@/components/common";
import { Badge } from "@/components/ui/card";
import { PersonalHeinzels } from "@/heinzel/PersonalHeinzels";
import { format, type MessageKey, t } from "@/i18n";
import { contractPath } from "@/layout/navigation";

type State = SchemaHeinzelSummary["state"];

/** Each state as a word with a mark, never colour alone. */
const states: Record<State, { tone: "ok" | "neutral" | "warn" | "bad"; mark: string; label: MessageKey }> = {
  scheduled: { tone: "ok", mark: "✔", label: "heinzel.state.scheduled" },
  manual: { tone: "neutral", mark: "○", label: "heinzel.state.manual" },
  blocked: { tone: "warn", mark: "▲", label: "heinzel.state.blocked" },
  broken: { tone: "bad", mark: "✖", label: "heinzel.state.broken" },
};

function StateBadge({ state }: { state: State }) {
  const { tone, mark, label } = states[state];
  return (
    <Badge tone={tone}>
      <span aria-hidden className="mr-1">
        {mark}
      </span>
      {t(label)}
    </Badge>
  );
}

const fileName = (file: string) => file.split("/").pop() ?? file;

export function Heinzelmaennchen() {
  const list = useHeinzelmaennchen();
  return (
    <Page title={t("heinzel.title")} intro={t("heinzel.intro")}>
      <PersonalHeinzels />
      <Loaded query={list}>
        {(shown) =>
          shown.heinzelmaennchen.length === 0 ? (
            <Empty text={t("heinzel.empty")} commands={shown.next.map((step) => step.command)} />
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">{t("heinzel.title")}</caption>
                <thead className="bg-muted text-xs text-muted-foreground">
                  <tr>
                    <th scope="col" className="p-2">
                      {t("heinzel.name")}
                    </th>
                    <th scope="col" className="p-2">
                      {t("heinzel.contract")}
                    </th>
                    <th scope="col" className="p-2">
                      {t("heinzel.owner")}
                    </th>
                    <th scope="col" className="p-2">
                      {t("heinzel.stateColumn")}
                    </th>
                    <th scope="col" className="p-2">
                      {t("heinzel.lastRun")}
                    </th>
                    <th scope="col" className="p-2">
                      {t("heinzel.nextRun")}
                    </th>
                    <th scope="col" className="p-2">
                      {t("heinzel.drafts")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shown.heinzelmaennchen.map((heinzel) => {
                    const qualified = `${heinzel.namespace}:${heinzel.name}`;
                    return (
                      <tr key={qualified} className="border-t border-border align-top">
                        <td className="p-2">
                          <Link to={contractPath(qualified)} className="font-medium underline-offset-2 hover:underline">
                            {heinzel.title ?? heinzel.name}
                          </Link>
                        </td>
                        <td className="p-2">
                          <code className="font-mono text-xs" title={heinzel.file}>
                            {fileName(heinzel.file)}
                          </code>
                        </td>
                        <td className="p-2">
                          {heinzel.namespace === "team" ? t("heinzel.ownerTeam") : t("heinzel.ownerMe")}
                        </td>
                        <td className="p-2">
                          <StateBadge state={heinzel.state} />
                        </td>
                        <td className="p-2 text-muted-foreground">
                          {heinzel.last_run ? format.dateTime(heinzel.last_run) : t("heinzel.never")}
                        </td>
                        <td className="p-2 text-muted-foreground">
                          {heinzel.next_run ? format.dateTime(heinzel.next_run) : t("heinzel.byHand")}
                        </td>
                        <td className="p-2">
                          {heinzel.drafts > 0 ? (
                            <CopyCommand command={`hz heinzel drafts --name ${qualified}`} />
                          ) : (
                            <span className="text-muted-foreground">{t("heinzel.noDrafts")}</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        }
      </Loaded>
    </Page>
  );
}
