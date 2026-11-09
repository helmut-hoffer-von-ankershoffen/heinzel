import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type SchemaMcpHandoffPlan, unwrap } from "@/api/client";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

type Action = SchemaMcpHandoffPlan["action"];

/** Inspect observations and prepare a plan. Applying it always requires an explicit review in the local CLI. */
export function McpSettings() {
  const [input, setInput] = useState("");
  const [registration, setRegistration] = useState("");
  const availability = useQuery({
    queryKey: ["mcp", "availability"],
    queryFn: ({ signal }) => unwrap(api.GET("/mcp/lifecycle", { signal, cache: "no-store" })),
  });
  const hostAvailable =
    availability.isSuccess && availability.fetchStatus === "idle" && availability.data.planning_available;
  const status = useQuery({
    queryKey: ["mcp", "status", registration],
    enabled: hostAvailable && registration !== "",
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/mcp/lifecycle/{registration}", { params: { path: { registration } }, signal, cache: "no-store" }),
      ),
  });
  const queryClient = useQueryClient();
  const canPlan = hostAvailable && status.isSuccess && status.fetchStatus === "idle";
  // Counts advance even when a refresh returns identical data within the same millisecond.
  const revision = [
    queryClient.getQueryState(["mcp", "availability"])?.dataUpdateCount,
    queryClient.getQueryState(["mcp", "status", registration])?.dataUpdateCount,
  ].join(":");
  return (
    <section aria-label={t("mcpSettings.title")} className="space-y-4 text-sm">
      <h3 className="text-ui font-medium">{t("mcpSettings.title")}</h3>
      <section aria-label={t("mcpSettings.clients")} className="space-y-2">
        <h4 className="font-medium">{t("mcpSettings.clients")}</h4>
        <ul>
          {(["claude", "codex", "cursor"] as const).map((client) => (
            <li key={client}>
              {t(`mcpSettings.clientName.${client}`)}: {t("mcpSettings.clientNotChecked")}
            </li>
          ))}
        </ul>
        <p>{t("mcpSettings.clientEvidence")}</p>
      </section>
      {availability.isPending && <p role="status">{t("mcpSettings.loading")}</p>}
      {availability.isError && <p role="alert">{t("mcpSettings.statusError")}</p>}
      {availability.data && !hostAvailable && <p role="status">{t("mcpSettings.unavailable")}</p>}
      {hostAvailable && (
        <>
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (input === registration) void status.refetch();
              else setRegistration(input);
            }}
          >
            <label className="flex flex-col gap-1">
              {t("mcpSettings.registration")}
              <input
                className="h-9 rounded-lg border border-border bg-input px-2"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                pattern="[A-Za-z0-9_\-]{1,96}"
                maxLength={96}
                required
              />
            </label>
            <Button type="submit" size="sm" variant="outline">
              {t("mcpSettings.readStatus")}
            </Button>
          </form>
          {status.isError && <p role="alert">{t("mcpSettings.statusError")}</p>}
          {status.data?.host === "unavailable" && <p role="status">{t("mcpSettings.targetUnavailable")}</p>}
          {canPlan && status.data?.host === "observed" && (
            <>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
                <dt>{t("mcpSettings.manager")}</dt>
                <dd>{t(`mcpSettings.state.${status.data.manager}`)}</dd>
                <dt>{t("mcpSettings.worker")}</dt>
                <dd>{t(`mcpSettings.state.${status.data.worker}`)}</dd>
                <dt>{t("mcpSettings.transport")}</dt>
                <dd>{t(`mcpSettings.state.${status.data.transport}`)}</dd>
                <dt>{t("mcpSettings.readiness")}</dt>
                <dd>{t(`mcpSettings.state.${status.data.mcp_readiness}`)}</dd>
                <dt>{t("mcpSettings.client")}</dt>
                <dd>{t(`mcpSettings.state.${status.data.client}`)}</dd>
              </dl>
              {!status.data.actions?.length && <p role="status">{t("mcpSettings.noActions")}</p>}
              <PlanActions
                key={`${registration}:${revision}`}
                registration={registration}
                actions={status.data.actions ?? []}
              />
            </>
          )}
        </>
      )}
    </section>
  );
}

function PlanActions({ registration, actions }: { registration: string; actions: Action[] }) {
  const prepare = useMutation({
    mutationFn: (action: Action) =>
      unwrap(
        api.POST("/mcp/lifecycle/{registration}/prepare", {
          params: { path: { registration }, query: { action } },
          cache: "no-store",
        }),
      ),
  });
  const queryClient = useQueryClient();
  const reset = prepare.reset;
  useEffect(
    () =>
      queryClient.getQueryCache().subscribe((event) => {
        const key = event.query.queryKey;
        if (
          event.type === "updated" &&
          event.action.type === "fetch" &&
          key[0] === "mcp" &&
          (key[1] === "availability" || (key[1] === "status" && key[2] === registration))
        ) {
          // Invalidate even a refresh cancelled before React renders its fetching state.
          reset();
        }
      }),
    [queryClient, registration, reset],
  );
  const plan =
    prepare.data?.registration === registration && actions.includes(prepare.data.action) ? prepare.data : undefined;
  return (
    <>
      <div className="flex flex-wrap gap-2">
        {actions.map((action) => (
          <Button
            key={action}
            variant="outline"
            size="sm"
            disabled={prepare.isPending}
            onClick={() => prepare.mutate(action)}
          >
            {t(`mcpSettings.prepare.${action}`)}
          </Button>
        ))}
      </div>
      {prepare.isError && <p role="alert">{t("mcpSettings.planError")}</p>}
      {plan && <LocalReview key={plan.id} plan={plan} />}
    </>
  );
}

function LocalReview({ plan }: { plan: SchemaMcpHandoffPlan }) {
  const [outcome, setOutcome] = useState<"copying" | "copied" | "error" | null>(null);
  const desktop = (window as Window & { heinzelDesktop?: { mcpHandoff?: (id: string) => Promise<boolean> } })
    .heinzelDesktop;
  const owning = plan.session === "owning-session";
  const operation = plan.action === "install" ? "review-install" : plan.action;
  const command = owning
    ? `${plan.action} --handoff ${plan.handoff}`
    : `hz system mcp ${operation} ${plan.registration} --handoff ${plan.handoff}`;
  return (
    <div className="space-y-2">
      <p>{t(owning ? "mcpSettings.owningReview" : "mcpSettings.localReview")}</p>
      {plan.action === "install" && <p>{t("mcpSettings.installReviewOnly")}</p>}
      {desktop?.mcpHandoff ? (
        <Button
          size="sm"
          variant="outline"
          disabled={outcome === "copying"}
          onClick={async () => {
            setOutcome("copying");
            try {
              setOutcome((await desktop.mcpHandoff?.(plan.id)) ? "copied" : null);
            } catch {
              setOutcome("error");
            }
          }}
        >
          {t("mcpSettings.openHandoff")}
        </Button>
      ) : (
        <details>
          <summary className="cursor-pointer">{t("mcpSettings.showCommand")}</summary>
          <code className="mt-2 block break-all rounded-lg bg-accent p-2">{command}</code>
        </details>
      )}
      {outcome === "copied" && <p role="status">{t("mcpSettings.copied")}</p>}
      {outcome === "error" && <p role="alert">{t("mcpSettings.planError")}</p>}
    </div>
  );
}
