// The summary: who you are, your team, the next contract run and the doctor's status.
import { Link } from "react-router";
import { useContracts, useDoctor, useJobs, useProfile, useTeam } from "@/api/queries";
import { CopyCommand, Loaded, Page, StatusBadge } from "@/components/common";
import { Card, CardTitle } from "@/components/ui/card";
import { format, t } from "@/i18n";

export function Home() {
  const profile = useProfile();
  const team = useTeam();
  const jobs = useJobs();
  const doctor = useDoctor();
  const contracts = useContracts();
  // A contract's daemon job is `workflow:<namespace>-<name>`; other workflow jobs (pruning) are housekeeping.
  const contractJobs = new Set(contracts.data?.contracts.map((c) => `workflow:${c.namespace}-${c.name}`) ?? []);
  return (
    <Page title={t("home.title")}>
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardTitle>{t("home.profile")}</CardTitle>
          <Loaded query={profile}>
            {(me) => (
              <div className="flex flex-col items-start gap-1 text-sm">
                <p className="text-lg font-semibold">{me.handle ?? "—"}</p>
                <p className="text-muted-foreground">
                  {[me.role, me.timezone, me.working_hours].filter(Boolean).join(" · ")}
                </p>
                {!me.complete && (
                  <>
                    <p>{t("home.profileMissing", { missing: me.missing.join(", ") })}</p>
                    <CopyCommand command={me.fix} />
                  </>
                )}
              </div>
            )}
          </Loaded>
        </Card>
        <Card>
          <CardTitle>{t("home.team")}</CardTitle>
          <Loaded query={team}>
            {(data) =>
              data.exists ? (
                <Link to="/team" className="text-sm text-primary underline-offset-2 hover:underline">
                  {data.name} · {data.members?.length ?? 0}
                </Link>
              ) : (
                <div className="flex flex-col items-start gap-1 text-sm">
                  <p className="text-muted-foreground">{t("home.noTeam")}</p>
                  <CopyCommand command="hz team join <url>" />
                </div>
              )
            }
          </Loaded>
        </Card>
        <Card>
          <CardTitle>{t("home.next")}</CardTitle>
          <Loaded query={jobs}>
            {(list) => {
              const next = list
                .filter((job) => contractJobs.has(job.job) && job.next_run)
                .sort((a, b) => (a.next_run ?? "").localeCompare(b.next_run ?? ""))[0];
              return next?.next_run ? (
                <p className="text-sm">
                  <code className="font-mono text-xs">{next.job}</code> · {format.dateTime(next.next_run)}
                </p>
              ) : (
                <p className="text-sm text-muted-foreground">{t("home.noRun")}</p>
              );
            }}
          </Loaded>
        </Card>
        <Card>
          <CardTitle>{t("home.health")}</CardTitle>
          <Loaded query={doctor}>
            {(report) => (
              <Link to="/doctor" className="inline-flex items-center gap-2 text-sm">
                {t("doctor.status")} <StatusBadge status={report.status} />
              </Link>
            )}
          </Loaded>
        </Card>
      </div>
    </Page>
  );
}
