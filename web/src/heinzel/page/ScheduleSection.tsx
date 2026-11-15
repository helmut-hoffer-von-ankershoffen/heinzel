// The schedule on the Heinzel's page: the summary hz writes in words, whether it is on and when it runs next. No cron
// text and no preview hash: those stay in the requests that need them (`--json` shows them).
import { useId } from "react";
import type { components } from "@/api/schema";
import { type MessageKey, t } from "@/i18n";
import { when } from "./when";

type Schedule = components["schemas"]["WakeScheduleView"];

export function ScheduleSection({ schedule }: { schedule: Schedule | null | undefined }) {
  const heading = useId();
  const next = schedule?.status === "active" ? when(schedule.next_wake) : null;
  const last = when(schedule?.last_run);
  return (
    <section aria-labelledby={heading} className="flex flex-col gap-1">
      <h2 id={heading} className="text-base font-semibold">
        {t("page.schedule")}
      </h2>
      {schedule ? (
        <>
          <p className="text-sm">
            {t(`page.scheduleState.${schedule.status}` as MessageKey)} · {schedule.summary}
          </p>
          {next && <p className="text-sm text-muted-foreground">{t("page.nextRun", { when: next })}</p>}
          {last && <p className="text-sm text-muted-foreground">{t("page.lastRun", { when: last })}</p>}
        </>
      ) : (
        <p className="text-sm text-muted-foreground">{t("page.scheduleNone")}</p>
      )}
    </section>
  );
}
