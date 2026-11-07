import { useQuery } from "@tanstack/react-query";
import { Download, LoaderCircle, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { FooterItem } from "@/components/FooterItem";
import { PaneAction } from "@/components/PaneActions";
import { t } from "@/i18n";
import { AutoUpdateToggle } from "@/updates/AutoUpdateToggle";
import { useAutoUpdateStatus } from "@/updates/autoUpdateApi";
import { canVerifyUpdate, finishUpdate, pendingUpdate, readUpdateStatus, startUpdate } from "@/updates/controller";

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
  const plan = useAutoUpdateStatus().data;
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
  // The auto-update plan is age-filtered and can retain failed checks. These are known updates,
  // never an exhaustive claim that every installed plugin has been checked or is current.
  const core = status?.available ? status.latest : plan?.core;
  const updates = [
    ...(core ? [`hz ${core}`] : []),
    ...Object.entries(plan?.plugins ?? {}).map(([name, version]) => `${name} ${version}`),
  ];
  const available = updates.length > 0;
  const installed = status?.installed_at ? new Date(status.installed_at) : null;
  const installedDate =
    installed && !Number.isNaN(installed.getTime())
      ? installed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
      : null;
  const canUpdate = status?.supported && !busy && recovery === "none";
  const label = available ? t("updates.availableLabel") : t("updates.title");
  const summary = phase ?? message ?? (available ? null : t("updates.unknown"));
  return (
    <FooterItem
      label={label}
      state={problem ? "bad" : available ? "warn" : "off"}
      icon={busy ? LoaderCircle : Download}
      className={available && !problem ? "animate-pulse text-bad" : undefined}
      disabled={busy}
      actions={
        <>
          {recovery === "verify" && (
            <PaneAction icon={ShieldCheck} disabled={busy} onClick={() => void run(true)}>
              {t("updates.verify")}
            </PaneAction>
          )}
          {canUpdate && (
            <PaneAction icon={Download} onClick={() => void run(false)}>
              {available ? t("updates.action") : t("updates.manualAction")}
            </PaneAction>
          )}
        </>
      }
    >
      <div className="space-y-2 px-1.5 pb-1.5">
        {summary && <p role="status">{summary}</p>}
        {available && <p>{t("updates.known", { updates: updates.join(", ") })}</p>}
        {status && <p>{t("updates.installedVersion", { version: status.current })}</p>}
        {status && (
          <p className="text-xs text-muted-foreground">
            {installedDate ? t("updates.installedDate", { date: installedDate }) : t("updates.installedDateUnknown")}
          </p>
        )}
        {status?.reason && <p className="text-xs text-muted-foreground">{status.reason}</p>}
        {problem && (
          <p role="alert" className="text-bad">
            {problem}
          </p>
        )}
        <AutoUpdateToggle />
      </div>
    </FooterItem>
  );
}
