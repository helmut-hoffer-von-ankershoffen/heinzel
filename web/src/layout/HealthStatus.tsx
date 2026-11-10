import { HeartPulse, RefreshCw, Stethoscope } from "lucide-react";
import { useFooterHealth } from "@/api/resources";
import { FooterItem, type State } from "@/components/FooterItem";
import { PaneAction } from "@/components/PaneActions";
import { t } from "@/i18n";

export function HealthStatus() {
  const query = useFooterHealth();
  const report = query.isError ? undefined : query.data;
  const checks = Object.entries(report?.checks ?? {}).flatMap(([module, items]) =>
    items.map((check) => ({ ...check, module })),
  );
  const state: State =
    !report || !checks.length ? "off" : report.status === "down" ? "bad" : report.status === "degraded" ? "warn" : "ok";
  const problems = checks
    .filter((check) => check.status !== "up")
    .sort((a, b) => Number(b.status === "down") - Number(a.status === "down"));
  const summary = t("health.summary", {
    up: checks.filter((check) => check.status === "up").length,
    degraded: checks.filter((check) => check.status === "degraded").length,
    down: checks.filter((check) => check.status === "down").length,
  });
  return (
    <FooterItem
      label={t("health.title")}
      state={state}
      icon={HeartPulse}
      actions={
        <>
          <PaneAction icon={Stethoscope} to="/doctor">
            {t("health.doctor")}
          </PaneAction>
          <PaneAction icon={RefreshCw} disabled={query.isFetching} onClick={() => void query.refetch()}>
            {t("health.refresh")}
          </PaneAction>
        </>
      }
    >
      <div className="space-y-2 px-1.5 pb-1.5">
        {query.isPending ? (
          <p>{t("health.loading")}</p>
        ) : query.isError ? (
          <p role="status">{t("health.failed")}</p>
        ) : checks.length ? (
          <p>{summary}</p>
        ) : (
          <p>{t("health.empty")}</p>
        )}
        {problems.slice(0, 3).map((check) => (
          <div key={`${check.module}:${check.name}`} className="text-xs">
            <p className="font-medium">
              {check.module} · {check.name}
            </p>
            <p className="break-words text-muted-foreground">{check.detail}</p>
            {check.fix && check.fix !== check.detail && <p className="mt-0.5 break-words">{check.fix}</p>}
          </div>
        ))}
        {problems.length > 3 && <p>{t("health.more", { count: problems.length - 3 })}</p>}
      </div>
    </FooterItem>
  );
}
