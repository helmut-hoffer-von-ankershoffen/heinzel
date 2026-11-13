import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, LoaderCircle, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { onMenuAction } from "@/about/menu";
import type { SchemaUpdatePlan } from "@/api/client";
import { FooterItem } from "@/components/FooterItem";
import { PaneAction } from "@/components/PaneActions";
import { Progress, type ProgressSegment } from "@/components/Progress";
import { t } from "@/i18n";
import { type Phase, requestDrivenOperation } from "@/lib/operationRun";
import { AutoUpdateToggle } from "@/updates/AutoUpdateToggle";
import { autoLoadInstalledCore } from "@/updates/autoLoad";
import { checkNow, KEY, useAutoUpdateStatus } from "@/updates/autoUpdateApi";
import {
  canVerifyUpdate,
  finishUpdate,
  loadInstalledCore,
  pendingUpdate,
  readUpdatePlan,
  readUpdateStatus,
  startUpdate,
  type UpdateAnswers,
} from "@/updates/controller";
import { UpdatePlanDetails } from "@/updates/UpdatePlanDetails";
import { describe, partSegments } from "@/updates/updateState";
import { reloadHeld, workOnScreen } from "@/updates/work";

/** The update as the operation dialog asks for it: the call the person confirms there, after seeing its plan. */
const updateCall = () => ({
  path: "/system/update",
  method: "POST" as const,
  query: { yes: true },
  command: "hz system update --yes",
  summary: t("updates.dialogTitle"),
  read_only: false,
});

/** What the operation dialog hears of an update it asked for: each step, and the answers given there. */
type Agreed = { report: (phases: Phase[]) => void; answers: UpdateAnswers };

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
  // "Check for Updates…" in the desktop app menu opens this pane and does the same check.
  useEffect(
    () =>
      onMenuAction(["updates"], () => {
        setOpenRequest((count) => count + 1);
        runCheck();
      }),
    [runCheck],
  );
  const [planning, setPlanning] = useState(false);
  /**
   * `installed`: load that already installed core with a verified restart instead of installing anything.
   * `agreed`: the update the person confirmed in the operation dialog, which hears every step and how it ended.
   */
  const run = useCallback(async (resume: boolean, installed?: string, agreed?: Agreed) => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setProblem(null);
    setSteps([]);
    try {
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
                agreed?.report(phases);
              },
              agreed?.answers,
            );
      if (mounted.current) {
        setSteps([]);
        setMessage(answer);
        setRecovery(updateRecoveryState());
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
      if (agreed) throw error; // the dialog that asked shows why, with the steps so far
    } finally {
      active.current = null;
      if (mounted.current) {
        setBusy(false);
        setPhase(null);
      }
    }
  }, []);
  /** The update gesture: read what would change, then ask once in the operation dialog; nothing runs before. */
  const ask = useCallback(async () => {
    if (active.current || planning) return;
    setPlanning(true);
    setProblem(null);
    let plan: SchemaUpdatePlan;
    try {
      plan = await readUpdatePlan(AbortSignal.timeout(30_000));
    } catch (error) {
      // Nobody can agree to a plan they did not see: say why it couldn't be read and offer nothing to confirm.
      if (mounted.current) {
        setPlanning(false);
        setProblem(`${t("updates.plan.unread")} ${error instanceof Error ? error.message : ""}`.trim());
      }
      return;
    }
    if (!mounted.current) return;
    setPlanning(false);
    const answers: UpdateAnswers = { plan: plan.token ?? "", desktop: plan.desktop === true };
    requestDrivenOperation(updateCall(), {
      consent: [t("updates.confirm.consent"), t("updates.confirm.pins")],
      details: (
        <UpdatePlanDetails
          plan={plan}
          onDesktop={(on) => {
            answers.desktop = on;
          }}
        />
      ),
      action: t("updates.confirm.action"),
      runningTitle: t("updates.dialogTitle"),
      doneTitle: t("updates.confirm.done"),
      running: t("updates.confirm.running"),
      run: (report) => run(false, undefined, { report, answers }),
    });
  }, [run, planning]);
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
  const installedDate =
    installed && !Number.isNaN(installed.getTime())
      ? installed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
      : null;
  const canUpdate = status?.supported && !busy && !planning && recovery === "none" && !installedCore;
  const canLoad = installedCore !== null && !busy && recovery === "none";
  const label = view.available ? t("updates.availableLabel") : t("updates.title");
  return (
    <FooterItem
      label={label}
      state={view.tone}
      icon={busy ? LoaderCircle : Download}
      className={view.available && !problem ? "animate-pulse text-bad" : view.allCurrent ? "text-ok" : undefined}
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
          {canUpdate && (
            <PaneAction icon={Download} primary={view.count > 0} onClick={() => void ask()}>
              {view.count > 1
                ? t("updates.allAction")
                : view.count === 1
                  ? t("updates.action")
                  : t("updates.manualAction")}
            </PaneAction>
          )}
          {!busy && (
            <PaneAction icon={RefreshCw} disabled={check.isPending} onClick={() => runCheck()}>
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
            </div>
          </div>
        ))}
        {planning && <p role="status">{t("updates.planning")}</p>}
        <Progress label={t("updates.progress")} segments={segments} />
        {status && (
          <p className="text-xs text-muted-foreground">
            {installedDate ? t("updates.installedDate", { date: installedDate }) : t("updates.installedDateUnknown")}
          </p>
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
}
