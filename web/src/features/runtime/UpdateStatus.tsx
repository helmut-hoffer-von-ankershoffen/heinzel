import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, LoaderCircle, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { onMenuAction } from "@/about/menu";
import type { SchemaUpdatePlan } from "@/api/client";
import { FooterItem } from "@/components/FooterItem";
import { PaneAction } from "@/components/PaneActions";
import { Progress, type ProgressSegment } from "@/components/Progress";
import { Button } from "@/components/ui/button";
import { AutoUpdateToggle } from "@/features/runtime/updates/AutoUpdateToggle";
import { autoLoadInstalledCore } from "@/features/runtime/updates/autoLoad";
import { checkNow, KEY, useAutoUpdateStatus } from "@/features/runtime/updates/autoUpdateApi";
import {
  canVerifyUpdate,
  finishUpdate,
  loadInstalledCore,
  pendingUpdate,
  readUpdatePlan,
  readUpdateStatus,
  startUpdate,
  type UpdateAnswers,
  updateDesktopApp,
} from "@/features/runtime/updates/controller";
import { desktopRestartTarget, localMacClient, useDesktopCountdown } from "@/features/runtime/updates/desktopCountdown";
import { PluginUnpin } from "@/features/runtime/updates/PluginUnpin";
import { UpdatePlanDetails } from "@/features/runtime/updates/UpdatePlanDetails";
import { describe, partSegments } from "@/features/runtime/updates/updateState";
import { reloadHeld, saveOpenWork, workOnScreen } from "@/features/runtime/updates/work";
import { t } from "@/i18n";
import {
  OperationCancelled,
  type Phase,
  requestDrivenOperation,
  startOperation,
  useOperationRun,
} from "@/lib/operationRun";
import { zoneDateTime } from "@/lib/zoneTime";

/** The explicit update action and its progress dialog share the same operation. */
const updateCall = () => ({
  path: "/system/update",
  method: "POST" as const,
  query: { yes: true },
  command: "hz system update --yes",
  summary: t("updates.dialogTitle"),
  read_only: false,
});

type ManualUpdate = {
  report: (phases: Phase[]) => void;
  desktop: boolean;
  reviewedToken?: string;
  showChangedPlan: (plan: SchemaUpdatePlan) => void;
};

function updateRecoveryState(): "none" | "inspect" | "verify" {
  const receipt = pendingUpdate();
  return receipt ? (canVerifyUpdate(receipt) ? "verify" : "inspect") : "none";
}

/** Inspect the pane first; its update button uses the existing installer and verified lifecycle. */
export function UpdateStatus() {
  const planQuery = useAutoUpdateStatus();
  const plan = planQuery.data;
  const watchDesktop = Boolean(plan?.enabled && localMacClient());
  const query = useQuery({
    queryKey: ["web", "update-status"],
    queryFn: ({ signal }) => readUpdateStatus(signal),
    refetchInterval: watchDesktop ? 2_000 : 60_000,
    refetchIntervalInBackground: watchDesktop,
    retry: false,
  });
  const queryClient = useQueryClient();
  const operation = useOperationRun();
  const updateDialogOpen = operation.stage !== "idle" && operation.call.path === "/system/update";
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<string | null>(null);
  // The steps the update reports as it runs (the same list the operation dialog shows), kept after a failure.
  const [steps, setSteps] = useState<Phase[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [recovery, setRecovery] = useState<"none" | "inspect" | "verify">("none");
  const [message, setMessage] = useState<string | null>(null);
  const [openRequest, setOpenRequest] = useState(0);
  // The updater checks both core and plugin news, with its server-side rate limit. Refresh both views afterwards.
  const check = useMutation({
    mutationFn: async () => {
      console.info(JSON.stringify({ event: "web.update.check", outcome: "start" }));
      return checkNow();
    },
    onSuccess: async (result) => {
      console.info(
        JSON.stringify({
          event: "web.update.check",
          outcome: "done",
          core_checked: result.core_checked,
          plugins_checked: result.plugins_checked,
        }),
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["web", "update-status"] }),
        queryClient.invalidateQueries({ queryKey: KEY }),
      ]);
    },
    onError: () => console.info(JSON.stringify({ event: "web.update.check", outcome: "failed" })),
  });
  const { mutate: runCheck } = check;
  // The desktop app older than this server: the server quits, replaces and reopens it on its own.
  const desktopUpdate = useMutation({
    mutationFn: (automatic: boolean) => {
      saveOpenWork();
      return updateDesktopApp(AbortSignal.timeout(15_000), automatic);
    },
    onSuccess: () => console.info(JSON.stringify({ event: "web.update.desktop", outcome: "started" })),
    onError: () => console.info(JSON.stringify({ event: "web.update.desktop", outcome: "failed" })),
  });
  const { mutate: runDesktopUpdate } = desktopUpdate;
  const restartDesktop = useCallback(() => runDesktopUpdate(true), [runDesktopUpdate]);
  // "Check for Updates…" in the desktop app menu opens this pane and does the same check.
  useEffect(
    () =>
      onMenuAction(["updates"], () => {
        setOpenRequest((count) => count + 1);
        runCheck();
      }),
    [runCheck],
  );
  const [includeDesktop, setIncludeDesktop] = useState(true);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewPlan, setPreviewPlan] = useState<SchemaUpdatePlan>();
  const preview = useMutation({
    mutationFn: () => readUpdatePlan(AbortSignal.timeout(30_000)),
    onSuccess: setPreviewPlan,
  });
  /**
   * `installed`: load that already installed core with a verified restart instead of installing anything.
   * `manual`: an explicit update whose preparation and result appear in one dialog.
   */
  const run = useCallback(async (resume: boolean, installed?: string, manual?: ManualUpdate) => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setProblem(null);
    setSteps([]);
    try {
      let answers: UpdateAnswers | undefined;
      if (manual) {
        const reading: Phase = { id: "plan", label: t("updates.planning"), state: "running" };
        manual.report([reading]);
        let plan: SchemaUpdatePlan;
        try {
          plan = await readUpdatePlan(AbortSignal.any([controller.signal, AbortSignal.timeout(30_000)]));
        } catch (error) {
          manual.report([{ ...reading, state: "failed" }]);
          throw new Error(`${t("updates.plan.unread")} ${error instanceof Error ? error.message : ""}`.trim());
        }
        if (manual.reviewedToken && plan.token !== manual.reviewedToken) {
          if (mounted.current) manual.showChangedPlan(plan);
          throw new OperationCancelled(t("updates.plan.changed"));
        }
        answers = { plan: manual.reviewedToken ?? plan.token ?? "", desktop: manual.desktop && plan.desktop === true };
        manual.report([{ ...reading, state: "done" }]);
      }
      const onPhase = (phase: string) => {
        if (mounted.current) setPhase(phase);
      };
      const answer = resume
        ? await finishUpdate(controller.signal)
        : installed
          ? await loadInstalledCore(installed, controller.signal, onPhase, undefined, (phases) => {
              if (mounted.current) setSteps(phases);
            })
          : await startUpdate(
              controller.signal,
              onPhase,
              undefined,
              (phases) => {
                if (mounted.current) setSteps(phases);
                manual?.report(phases);
              },
              answers,
            );
      if (mounted.current) {
        setSteps([]);
        setMessage(answer);
        setRecovery(updateRecoveryState());
        if (manual) {
          setPreviewPlan(undefined);
          setPreviewOpen(false);
        }
      }
      return answer;
    } catch (error) {
      if (mounted.current) {
        setProblem(error instanceof Error ? error.message : t("updates.failed"));
        try {
          setRecovery(updateRecoveryState());
        } catch {
          setRecovery("inspect");
        }
      }
      if (manual) throw error; // the same dialog shows why, with the steps so far
    } finally {
      active.current = null;
      if (mounted.current) {
        setBusy(false);
        setPhase(null);
      }
    }
  }, []);
  const install = () => {
    if (active.current || preview.isPending) return;
    const requested = requestDrivenOperation(updateCall(), {
      consent: [],
      action: t("updates.action"),
      runningTitle: t("updates.dialogTitle"),
      doneTitle: t("updates.confirm.done"),
      running: t("updates.confirm.running"),
      run: (report) =>
        run(false, undefined, {
          report,
          desktop: includeDesktop,
          reviewedToken: previewPlan?.token ?? undefined,
          showChangedPlan: (plan) => {
            setPreviewPlan(plan);
            setPreviewOpen(true);
          },
        }),
    });
    if (requested)
      void startOperation(() => void queryClient.invalidateQueries({ queryKey: ["web", "update-status"] }));
  };
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
  const desktopTarget = desktopRestartTarget(status);
  const countdown = useDesktopCountdown(
    desktopTarget,
    !query.isError &&
      !planQuery.isError &&
      plan?.enabled === true &&
      status?.current === plan.version &&
      plan.phase === "idle" &&
      !busy &&
      !updateDialogOpen &&
      problem === null &&
      recovery === "none" &&
      !desktopUpdate.isPending &&
      !desktopUpdate.isSuccess,
    status?.idle !== false,
    restartDesktop,
  );
  // A newer core is installed but this server still runs the old one (a restart was missed): load it on its own once
  // per version across windows, when the server is idle, nothing is open or running here and no other window acts.
  const installedCore = status?.installed ?? null;
  const loadWaits = installedCore !== null && (status?.idle === false || reloadHeld() || workOnScreen());
  useEffect(() => {
    if (!installedCore || busy || recovery !== "none") return;
    let attempting = false;
    let done = false;
    const wait = window.setInterval(() => void start(), 2_000);
    async function start() {
      if (attempting || done) return;
      attempting = true;
      try {
        const outcome = await autoLoadInstalledCore(
          installedCore as string,
          (version) => run(false, version),
          () => readUpdateStatus(new AbortController().signal),
        );
        done = outcome !== "held" && outcome !== "elsewhere";
      } catch {
        // the status could not be read: ask again with the next round
      } finally {
        attempting = false;
        if (done) window.clearInterval(wait);
      }
    }
    void start();
    return () => {
      done = true;
      window.clearInterval(wait);
    };
  }, [installedCore, busy, recovery, run]);
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
  // The head's own steps while this window updates; while the server applies on its own it reports none, so the
  // parts it is changing show as running together.
  const segments: ProgressSegment[] =
    steps.length > 0
      ? steps.map(({ id, label, state }) => ({ id, label, state }))
      : phase === null && plan?.phase === "applying"
        ? partSegments(view.parts)
        : [];
  const installed = status?.installed_at ? new Date(status.installed_at) : null;
  const installedDate = installed && !Number.isNaN(installed.getTime()) ? zoneDateTime(installed) : null;
  const canUpdate = status?.supported && !busy && recovery === "none" && !installedCore;
  const canLoad = installedCore !== null && !busy && recovery === "none";
  const desktop = status?.desktop?.behind ? status.desktop : null;
  const canUpdateDesktop = desktop !== null && !busy && !desktopUpdate.isPending && !desktopUpdate.isSuccess;
  const manualRestart = Boolean(desktopTarget && countdown.manual && !desktopUpdate.isSuccess);
  const label = manualRestart
    ? t("updates.desktopRestartRequired")
    : view.available || desktop
      ? t("updates.availableLabel")
      : t("updates.title");
  const footer = (
    <FooterItem
      label={label}
      state={view.tone}
      icon={busy || desktopUpdate.isPending ? LoaderCircle : Download}
      className={
        (view.available || desktop) && !problem ? "animate-pulse text-bad" : view.allCurrent ? "text-ok" : undefined
      }
      disabled={busy}
      openRequest={openRequest}
      actions={
        // The lead is what the pane is for: finish a pending update, else install one; checking again is an icon.
        <>
          {recovery === "verify" && (
            <PaneAction icon={RotateCcw} primary disabled={busy} onClick={() => void run(true)}>
              {t("updates.verify")}
            </PaneAction>
          )}
          {canLoad && (
            <PaneAction icon={RotateCcw} primary onClick={() => void run(false, installedCore)}>
              {t("updates.loadInstalled", { version: installedCore })}
            </PaneAction>
          )}
          {canUpdateDesktop && (
            <PaneAction
              icon={manualRestart ? RotateCcw : Download}
              primary={view.count === 0}
              onClick={() => {
                countdown.cancel();
                runDesktopUpdate(false);
              }}
            >
              {t(manualRestart ? "updates.desktopRestartAction" : "updates.desktopAction")}
            </PaneAction>
          )}
          {canUpdate && (
            <PaneAction icon={Download} primary={view.count > 0} disabled={preview.isPending} onClick={install}>
              {view.count > 1
                ? t("updates.allAction")
                : view.count === 1
                  ? t("updates.action")
                  : t("updates.manualAction")}
            </PaneAction>
          )}
          {!busy && (
            <PaneAction
              icon={RefreshCw}
              spinning={check.isPending}
              refreshFailed={check.isError}
              disabled={check.isPending}
              onClick={() => runCheck()}
            >
              {t("updates.check")}
            </PaneAction>
          )}
        </>
      }
    >
      <div className="space-y-2 px-1.5 pb-1.5">
        {installedCore && status && (
          <div>
            <p role="status">
              {busy
                ? t("updates.loadingInstalled", { version: installedCore })
                : t("updates.installedNotLoaded", { version: installedCore, running: status.current })}
            </p>
            {loadWaits && !busy && <p className="text-xs text-muted-foreground">{t("updates.loadInstalledLater")}</p>}
          </div>
        )}
        {view.rows.map((row) => (
          <div key={row.id} className="flex items-start gap-2">
            {row.busy && <LoaderCircle aria-hidden className="mt-0.5 size-3.5 shrink-0 animate-spin" />}
            <div className="min-w-0">
              <p role={row.alert ? "alert" : "status"} className={row.tone === "bad" ? "text-bad" : undefined}>
                {row.text}
              </p>
              {row.detail && <p className="text-xs text-muted-foreground">{row.detail}</p>}
              {row.unpin && <PluginUnpin name={row.unpin} />}
            </div>
          </div>
        ))}
        {desktop && status && (
          <div>
            <p role="status" className="text-bad">
              {desktopUpdate.isSuccess
                ? t("updates.desktopStarted")
                : t("updates.desktopBehind", { app: desktop.version ?? "", version: status.current })}
            </p>
            {!desktopUpdate.isSuccess && (
              <p className="text-xs text-muted-foreground">
                {t(manualRestart ? "updates.desktopRestartManual" : "updates.desktopHow")}
              </p>
            )}
            {desktopUpdate.error && (
              <p role="alert" className="text-bad">
                {desktopUpdate.error.message}
              </p>
            )}
          </div>
        )}
        {canUpdate && (
          <details
            open={previewOpen}
            onToggle={(event) => {
              const { open } = event.currentTarget;
              setPreviewOpen(open);
              if (open && !preview.isPending && !previewPlan) preview.mutate();
            }}
          >
            <summary className="cursor-pointer">{t("updates.plan.details")}</summary>
            <p className="mt-1 text-xs text-muted-foreground">{t("updates.confirm.consent")}</p>
            <p className="text-xs text-muted-foreground">{t("updates.confirm.pins")}</p>
            {preview.isPending && <p role="status">{t("updates.planning")}</p>}
            {preview.isError && <p role="alert">{t("updates.plan.unread")}</p>}
            {previewPlan && (
              <UpdatePlanDetails plan={previewPlan} desktop={includeDesktop} onDesktop={setIncludeDesktop} />
            )}
          </details>
        )}
        <Progress label={t("updates.progress")} segments={segments} />
        {installedDate && (
          <p className="text-xs text-muted-foreground">{t("updates.installedDate", { date: installedDate })}</p>
        )}
        {status?.reason && <p className="text-xs text-muted-foreground">{status.reason}</p>}
        <AutoUpdateToggle />
        {check.isError && (
          <p role="alert" className="text-bad">
            {t("updates.checkFailed")}
          </p>
        )}
      </div>
    </FooterItem>
  );
  return (
    <>
      {countdown.seconds !== null && (
        <section
          aria-label={t("updates.desktopRestartTitle")}
          className="fixed top-16 right-4 z-[calc(var(--layer-modal,60)_+_10)] w-[min(24rem,calc(var(--hz-viewport-width,100vw)_-_2rem))] space-y-2 rounded-xl border border-foreground/20 bg-background p-4 text-sm text-foreground shadow-lg"
        >
          <h2 className="font-medium">{t("updates.desktopRestartTitle")}</h2>
          <p role="timer">{t("updates.desktopRestartCountdown", { seconds: countdown.seconds })}</p>
          <Button size="sm" variant="outline" onClick={countdown.cancel}>
            {t("updates.desktopRestartCancel")}
          </Button>
        </section>
      )}
      {footer}
    </>
  );
}
