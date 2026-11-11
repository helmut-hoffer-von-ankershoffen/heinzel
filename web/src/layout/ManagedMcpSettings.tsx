import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { isLocalServer } from "@/lib/browser";
import { confirmOperation } from "@/lib/operationRun";

/** The same profile-owned operation serves CLI and authenticated web; no renderer process control. */
export function ManagedMcpSettings() {
  const local = isLocalServer();
  const status = useQuery({
    queryKey: ["system", "mcp", "check"],
    enabled: local,
    retry: false,
    queryFn: async ({ signal }) => {
      const { data, error } = await api.GET("/system/mcp/check", { signal, cache: "no-store" });
      if (!data || error) throw new Error(t("managedMcp.unverified"));
      return data;
    },
  });
  async function change(kind: "start" | "stop") {
    try {
      await confirmOperation({
        method: "POST",
        path: `/system/mcp/${kind}`,
        query: {},
        body: { confirm: true },
        command: "",
        summary: t(`managedMcp.confirm.${kind}`),
        read_only: false,
      });
    } catch {
      // The existing confirmation dialog owns cancellation and failed-operation feedback.
    } finally {
      void status.refetch();
    }
  }
  const report = status.data;
  const state = status.isError ? "unverified" : report?.state;
  let message = t("managedMcp.unverified");
  if (!state) message = t("managedMcp.checking");
  else if (state === "unavailable") message = t("managedMcp.unavailable");
  else if (state === "not-created") message = t("managedMcp.notCreated");
  else if (state === "stopped" && report?.owned_server === "closed") message = t("managedMcp.stopped");
  else if (
    state === "ready" &&
    report?.readiness === "authenticated-initialized" &&
    report.ownership === "verified" &&
    typeof report.tools === "number"
  ) {
    message = t("managedMcp.ready", { count: report.tools });
  }
  return (
    <section aria-label={t("managedMcp.title")} className="space-y-4 text-sm">
      <h3 className="text-ui font-medium">{t("managedMcp.title")}</h3>
      <p className="text-muted-foreground">{t("managedMcp.description")}</p>
      {!local ? (
        <p>{t("managedMcp.local")}</p>
      ) : (
        <>
          <p role={state === "unverified" ? "alert" : "status"}>{message}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              className="min-h-11"
              disabled={status.isFetching}
              onClick={() => void status.refetch()}
            >
              {t("managedMcp.check")}
            </Button>
            {report?.can_start && state !== "unverified" && (
              <Button
                type="button"
                className="min-h-11"
                disabled={status.isFetching}
                onClick={() => void change("start")}
              >
                {t("managedMcp.start")}
              </Button>
            )}
            {report?.can_stop && report.state !== "stopped" && (
              <Button
                type="button"
                className="min-h-11"
                disabled={status.isFetching}
                onClick={() => void change("stop")}
              >
                {t("managedMcp.stop")}
              </Button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
