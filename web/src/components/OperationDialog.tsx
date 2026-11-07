// The confirmation and the progress of one write the app runs: the exact call, then the real steps it reports as they
// happen, then how it ended with the next step when it failed (ADR 0056). One dialog serves every status item,
// sidebar entry and setup button, so a footer that lays itself out again cannot hide a running install.
import { useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, Loader2, Play, RotateCw, TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { daemonStatusKey } from "@/api/daemon";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { type MessageKey, t } from "@/i18n";
import {
  dismissOperation,
  type OperationRun,
  type Phase,
  type PhaseState,
  startOperation,
  useOperationRun,
  usePrimaryHost,
} from "@/lib/operationRun";
import { cn } from "@/lib/utils";

const PERSISTENT = new Set(["/system/daemon/install", "/system/daemon/uninstall"]);
/** Safe, easily repeated writes the app runs at once, with plain words instead of the exact call. */
const QUICK = new Set(["/system/daemon/restart"]);
const copyOf = (path: string) => (path.endsWith("/uninstall") ? "uninstall" : "install");

const ICON: Record<PhaseState, typeof Check | null> = {
  pending: null,
  running: Loader2,
  done: Check,
  warn: TriangleAlert,
  skipped: null,
  failed: X,
};
const TONE: Record<PhaseState, string> = {
  pending: "text-muted-foreground",
  running: "text-foreground",
  done: "text-ok",
  warn: "text-warn",
  skipped: "text-muted-foreground",
  failed: "text-bad",
};

/** The steps as a segmented bar, one segment per planned step: never a percentage the head did not measure. */
function Steps({ phases, active }: Readonly<{ phases: Phase[]; active: boolean }>) {
  if (!phases.length) return null;
  const finished = phases.filter((phase) => ["done", "warn", "skipped"].includes(phase.state)).length;
  const running = phases.some((phase) => phase.state === "running");
  return (
    <div className="mt-3 space-y-2">
      <progress
        className="sr-only"
        aria-label={t("op.steps")}
        max={phases.length}
        value={finished}
        aria-valuetext={t("op.step", {
          done: Math.min(finished + (running ? 1 : 0), phases.length),
          total: phases.length,
        })}
      />
      <div aria-hidden className="flex gap-1">
        {phases.map((phase) => (
          <span
            key={phase.id}
            aria-hidden
            className={cn(
              "h-1.5 flex-1 rounded-full bg-muted",
              phase.state === "done" && "bg-ok",
              phase.state === "warn" && "bg-warn",
              phase.state === "failed" && "bg-bad",
              phase.state === "running" && active && "animate-pulse bg-primary",
            )}
          />
        ))}
      </div>
      <ol aria-label={t("op.steps")} className="space-y-1.5">
        {phases.map((phase) => {
          const Icon = ICON[phase.state];
          return (
            <li key={phase.id} className="flex items-start gap-2">
              <span
                aria-hidden
                className={cn("mt-0.5 flex size-4 shrink-0 items-center justify-center", TONE[phase.state])}
              >
                {Icon ? (
                  <Icon className={cn("size-4", phase.state === "running" && "motion-safe:animate-spin")} />
                ) : (
                  <span className="size-1.5 rounded-full bg-current opacity-50" />
                )}
              </span>
              <span className={cn("min-w-0 flex-1", phase.state === "pending" && "text-muted-foreground")}>
                {phase.label}
                <span className="sr-only">: {t(`op.state.${phase.state}` as MessageKey)}</span>
                {phase.detail && <span className={cn("block text-xs", TONE[phase.state])}>{phase.detail}</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function titleOf(run: Exclude<OperationRun, { stage: "idle" }>): string {
  return PERSISTENT.has(run.call.path) ? t(`op.daemon.${copyOf(run.call.path)}.title` as MessageKey) : run.call.summary;
}

/** Mount once per place that can start a write; only the first mounted shows the dialog. */
export function OperationDialog() {
  const run = useOperationRun();
  const client = useQueryClient();
  const primary = usePrimaryHost();
  const heading = useId();
  const box = useRef<HTMLDivElement>(null);
  const stage = run.stage;
  // The outcome is announced and the keyboard lands on it; the confirmation keeps the focus on Cancel.
  useEffect(() => {
    if (primary && (stage === "done" || stage === "failed")) box.current?.focus();
  }, [primary, stage]);
  const quick = run.stage !== "idle" && QUICK.has(run.call.path);
  const settled = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["ui"], refetchType: "all" }),
      client.invalidateQueries({ queryKey: daemonStatusKey, refetchType: "all" }),
      client.invalidateQueries({ queryKey: ["org", "calendar-reminders-status"], refetchType: "all" }),
    ]).then(() => undefined);
  const go = () => void startOperation(() => void settled());
  // A quick write needs no confirmation: asking for it starts it.
  useEffect(() => {
    if (primary && stage === "confirm" && quick) void startOperation(() => void settled());
  });
  if (!primary || run.stage === "idle" || (quick && run.stage === "confirm")) return null;
  const persistent = PERSISTENT.has(run.call.path);
  const copy = copyOf(run.call.path);
  const plain = (kind: "running" | "done" | "failed") => t(`op.daemon.restart.${kind}` as MessageKey);
  return createPortal(
    <div
      ref={box}
      tabIndex={-1}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={heading}
      data-operation={run.call.path}
      onKeyDown={(event) => {
        // A dialog it covers (the setup wizard) must not close, or move the focus away, because of what is typed here.
        event.stopPropagation();
        if (event.key === "Escape") dismissOperation();
      }}
      className="confirmation-dialog fixed z-(--layer-modal) max-h-[calc(var(--hz-viewport-height,100dvh)-2rem)] w-[min(28rem,calc(var(--hz-viewport-width,100vw)-2rem))] overflow-y-auto rounded-xl border border-border bg-popover p-4 text-sm shadow-xl outline-none"
    >
      <p id={heading} className="font-medium">
        {titleOf(run)}
      </p>
      {run.stage === "confirm" && (
        <>
          <p className="mt-1 text-muted-foreground">
            {persistent ? t(`op.daemon.${copy}.consent` as MessageKey) : t("contrib.confirm")}
          </p>
          {run.call.command && (
            <code className="mt-2 block rounded bg-muted px-2 py-1 font-mono text-xs break-all">
              {run.call.command}
            </code>
          )}
          <PaneActions align="end" words="all">
            <PaneAction key="cancel" icon={X} autoFocus onClick={dismissOperation}>
              {t("contrib.cancel")}
            </PaneAction>
            <PaneAction key="go" icon={Play} primary onClick={go}>
              {persistent ? t(`op.daemon.${copy}.button` as MessageKey) : t("contrib.run")}
            </PaneAction>
          </PaneActions>
        </>
      )}
      {run.stage === "running" && (
        <>
          <output className="mt-1 block text-muted-foreground">
            {run.task ??
              (quick ? plain("running") : persistent ? t(`op.daemon.${copy}.running` as MessageKey) : t("op.working"))}
          </output>
          <Steps phases={run.phases} active />
          <p className="mt-3 text-xs text-muted-foreground">{t("op.noStop")}</p>
          <PaneActions align="end" words="all">
            <PaneAction key="working" icon={Loader2} primary disabled aria-disabled>
              {quick ? plain("running") : persistent ? t(`op.daemon.${copy}.running` as MessageKey) : t("op.working")}
            </PaneAction>
          </PaneActions>
        </>
      )}
      {run.stage === "done" && (
        <>
          <output className="mt-1 flex items-start gap-2 text-ok">
            <Check aria-hidden className="mt-0.5 size-4 shrink-0" />
            {quick ? plain("done") : persistent ? t(`op.daemon.${copy}.done` as MessageKey) : t("op.done")}
          </output>
          <Steps phases={run.phases} active={false} />
          <PaneActions align="end" words="all">
            <PaneAction key="close" icon={Check} primary autoFocus onClick={dismissOperation}>
              {t("op.close")}
            </PaneAction>
          </PaneActions>
        </>
      )}
      {run.stage === "failed" && (
        <>
          <p role="alert" className="mt-1 flex items-start gap-2 text-bad">
            <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 break-words">{quick ? plain("failed") : run.error}</span>
          </p>
          {!quick && <Steps phases={run.phases} active={false} />}
          {!quick && (
            <p className="mt-3 text-xs text-muted-foreground">
              {run.phases.some((phase) => phase.state === "failed") ? t("op.failedNext") : t("op.lost")}
            </p>
          )}
          <PaneActions align="end" words="all">
            <PaneAction key="close" icon={X} onClick={dismissOperation}>
              {t("op.close")}
            </PaneAction>
            <PaneAction key="retry" icon={RotateCw} primary onClick={go}>
              {t("op.retry")}
            </PaneAction>
          </PaneActions>
        </>
      )}
    </div>,
    document.body,
  );
}
