import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, LoaderCircle, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { FooterItem } from "@/components/FooterItem";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { AutoUpdateToggle } from "@/updates/AutoUpdateToggle";
import { checkNow, KEY, useAutoUpdateStatus } from "@/updates/autoUpdateApi";
import { canVerifyUpdate, finishUpdate, pendingUpdate, readUpdateStatus, startUpdate } from "@/updates/controller";
import { describe } from "@/updates/updateState";

function updateRecoveryState(): "none" | "inspect" | "verify" {
  const receipt = pendingUpdate();
  return receipt ? (canVerifyUpdate(receipt) ? "verify" : "inspect") : "none";
}

/** Inspect the pane first; its update button uses the existing installer and verified lifecycle. */
export function UpdateStatus() {
  const query = useQuery({
    queryKey: ["web", "update-status"],
    queryFn: ({ signal }) => readUpdateStatus(signal),
    refetchInterval: 60_000,
    retry: false,
  });
  const planQuery = useAutoUpdateStatus();
  const plan = planQuery.data;
  const queryClient = useQueryClient();
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [recovery, setRecovery] = useState<"none" | "inspect" | "verify">("none");
  const [message, setMessage] = useState<string | null>(null);
  const run = useCallback(async (resume: boolean) => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setProblem(null);
    try {
      const answer = resume
        ? await finishUpdate(controller.signal)
        : await startUpdate(controller.signal, (phase) => {
            if (mounted.current) setPhase(phase);
          });
      if (mounted.current) {
        setMessage(answer);
        setRecovery(updateRecoveryState());
      }
    } catch (error) {
      if (mounted.current) {
        setProblem(error instanceof Error ? error.message : t("updates.failed"));
        try {
          setRecovery(updateRecoveryState());
        } catch {
          setRecovery("inspect");
        }
      }
    } finally {
      active.current = null;
      if (mounted.current) {
        setBusy(false);
        setPhase(null);
      }
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    const first = new AbortController();
    // StrictMode's discarded setup must not cancel the real observation or start a mutation.
    void Promise.resolve().then(() => {
      if (first.signal.aborted) return;
      try {
        if (pendingUpdate()) void run(true);
      } catch (error) {
        setProblem(error instanceof Error ? error.message : t("updates.failed"));
        setRecovery("inspect");
      }
    });
    return () => {
      mounted.current = false;
      first.abort();
      active.current?.abort();
    };
  }, [run]);
  const status = query.data;
  const view = describe({
    status,
    statusFailed: query.isError,
    plan,
    planFailed: planQuery.isError,
    now: new Date(),
    phase,
    message,
    problem,
  });
  const installed = status?.installed_at ? new Date(status.installed_at) : null;
  const installedDate =
    installed && !Number.isNaN(installed.getTime())
      ? installed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
      : null;
  const canUpdate = status?.supported && !busy && recovery === "none";
  const label = view.available ? t("updates.availableLabel") : t("updates.title");
  const retry = useMutation({
    mutationFn: async () => {
      await checkNow();
      await Promise.all([query.refetch(), queryClient.invalidateQueries({ queryKey: KEY })]);
    },
  });
  return (
    <FooterItem
      label={label}
      state={view.tone}
      icon={busy ? LoaderCircle : Download}
      className={view.available && !problem ? "animate-pulse text-bad" : view.allCurrent ? "text-ok" : undefined}
      disabled={busy}
    >
      <div className="space-y-2 px-1.5 pb-1.5">
        {view.rows.map((row) => (
          <div key={row.id} className="flex items-start gap-2">
            {row.busy && <LoaderCircle aria-hidden className="mt-0.5 size-3.5 shrink-0 animate-spin" />}
            <div className="min-w-0">
              <p role={row.alert ? "alert" : "status"} className={row.tone === "bad" ? "text-bad" : undefined}>
                {row.text}
              </p>
              {row.detail && <p className="text-xs text-muted-foreground">{row.detail}</p>}
            </div>
          </div>
        ))}
        {status && (
          <p className="text-xs text-muted-foreground">
            {installedDate ? t("updates.installedDate", { date: installedDate }) : t("updates.installedDateUnknown")}
          </p>
        )}
        {status?.reason && <p className="text-xs text-muted-foreground">{status.reason}</p>}
        <AutoUpdateToggle />
        {/* The actions stay at the bottom, under a divider, when late facts grow this above-anchored pane. */}
        {(recovery === "verify" || canUpdate || (view.canRetry && !busy)) && (
          <div className="flex flex-wrap gap-2 border-t border-border/70 pt-2">
            {recovery === "verify" && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(true)}>
                <RotateCcw aria-hidden /> {t("updates.verify")}
              </Button>
            )}
            {view.canRetry && !busy && (
              <Button size="sm" variant="outline" disabled={retry.isPending} onClick={() => retry.mutate()}>
                <RefreshCw aria-hidden className={retry.isPending ? "animate-spin" : undefined} /> {t("updates.retry")}
              </Button>
            )}
            {canUpdate && (
              <Button size="sm" variant="outline" onClick={() => void run(false)}>
                <Download aria-hidden />{" "}
                {view.count > 1
                  ? t("updates.allAction")
                  : view.count === 1
                    ? t("updates.action")
                    : t("updates.manualAction")}
              </Button>
            )}
          </div>
        )}
      </div>
    </FooterItem>
  );
}
