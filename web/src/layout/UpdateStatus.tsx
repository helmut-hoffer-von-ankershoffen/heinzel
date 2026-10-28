import { useQuery } from "@tanstack/react-query";
import { Download, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { FooterItem } from "@/components/FooterItem";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { finishUpdate, pendingUpdate, readUpdateStatus, startUpdate } from "@/updates/controller";

/** Inspect the pane first; its update button uses the existing installer and verified lifecycle. */
export function UpdateStatus() {
  const query = useQuery({
    queryKey: ["web", "update-status"],
    queryFn: ({ signal }) => readUpdateStatus(signal),
    refetchInterval: 60_000,
    retry: false,
  });
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [recovery, setRecovery] = useState(false);
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
        setRecovery(!!pendingUpdate());
      }
    } catch (error) {
      if (mounted.current) {
        setProblem(error instanceof Error ? error.message : t("updates.failed"));
        try {
          setRecovery(!!pendingUpdate());
        } catch {
          setRecovery(true);
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
        setRecovery(true);
      }
    });
    return () => {
      mounted.current = false;
      first.abort();
      active.current?.abort();
    };
  }, [run]);
  const status = query.data;
  const available = status?.available === true;
  const checked = status?.latest != null;
  const installed = status?.installed_at ? new Date(status.installed_at) : null;
  const installedDate =
    installed && !Number.isNaN(installed.getTime())
      ? installed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
      : null;
  const canUpdate = available && status?.supported && !busy && !recovery;
  const current = checked && status?.supported === true;
  const label = available ? t("updates.availableLabel") : current ? t("updates.currentLabel") : t("updates.title");
  const summary =
    phase ??
    message ??
    (available ? t("updates.available", { version: status?.latest ?? "" }) : !checked ? t("updates.unknown") : null);
  return (
    <FooterItem
      label={label}
      state={problem ? "bad" : available ? "warn" : current ? "ok" : "off"}
      icon={busy ? LoaderCircle : Download}
      className={available && !problem ? "animate-pulse text-bad" : current && !problem ? "text-ok" : undefined}
      disabled={busy}
    >
      <div className="space-y-2 px-1.5 pb-1.5">
        {summary && <p role="status">{summary}</p>}
        {status && <p>{t("updates.installedVersion", { version: status.current })}</p>}
        {status && (
          <p className="text-xs text-muted-foreground">
            {installedDate ? t("updates.installedDate", { date: installedDate }) : t("updates.installedDateUnknown")}
          </p>
        )}
        {status?.reason && <p className="text-xs text-muted-foreground">{status.reason}</p>}
        {canUpdate && (
          <Button size="sm" variant="outline" onClick={() => void run(false)}>
            {t("updates.action")}
          </Button>
        )}
        {problem && (
          <p role="alert" className="text-bad">
            {problem}
          </p>
        )}
        {recovery && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(true)}>
            {t("updates.verify")}
          </Button>
        )}
      </div>
    </FooterItem>
  );
}
