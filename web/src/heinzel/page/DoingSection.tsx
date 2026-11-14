// "How it is doing" on the Heinzel's page (canvas boards Profile and Doing): the state word and the counts hz computes
// for the session list (slice 5). The state comes from the service in its fixed precedence, never from the page.
import { useId } from "react";
import type { components } from "@/api/schema";
import { activityWord } from "@/heinzel/activity";
import { t } from "@/i18n";
import { when } from "./when";

type Summary = components["schemas"]["SessionSummary"];

export function DoingSection({ summary }: { summary: Summary | undefined }) {
  const heading = useId();
  if (!summary) return null;
  const rows: Array<[string, string]> = [
    [t("page.doing.waiting"), String(summary.waiting_count ?? 0)],
    [t("page.doing.runs"), String(summary.runs_last_hour ?? 0)],
    [t("page.doing.queued"), String(summary.queued_runs ?? 0)],
    [t("page.doing.approved"), when(summary.last_approved_at) ?? t("page.doing.never")],
  ];
  // Without a record of every run, only a failed last run is a fact worth a row.
  if (summary.last_run_failed) rows.push([t("page.doing.failed"), t("page.doing.failedYes")]);
  return (
    <section aria-labelledby={heading} className="flex flex-col gap-2">
      <h2 id={heading} className="text-base font-semibold">
        {t("page.doing")}
      </h2>
      <p className="text-sm font-medium">{activityWord(summary.activity)}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
