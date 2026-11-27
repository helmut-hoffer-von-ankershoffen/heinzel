// The confirmation and the progress of one write the app runs: the exact call, then the real steps it reports as they
// happen, then how it ended with the next step when it failed (ADR 0056). One dialog serves every status item,
// sidebar entry and setup button, so a footer that lays itself out again cannot hide a running install.
import { useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, EyeOff, Loader2, Play, RotateCw, TriangleAlert, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { daemonStatusKey } from "@/api/daemon";
import { useExpert } from "@/api/queries";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { Progress } from "@/components/Progress";
import { Button } from "@/components/ui/button";
import { type MessageKey, t } from "@/i18n";
import { daemonRestartResult } from "@/lib/daemonRestart";
import {
  dismissOperation,
  getRun,
  isOperationProgressHidden,
  LONG_FORM_VALUE,
  type OperationRun,
  type Phase,
  type PhaseState,
  recoverOperation,
  setOperationProgressHidden,
  startOperation,
  useOperationRun,
  usePrimaryHost,
} from "@/lib/operationRun";
import { cn } from "@/lib/utils";

const PERSISTENT = new Set(["/system/daemon/install", "/system/daemon/uninstall"]);
/** Safe, easily repeated writes the app runs at once, with plain words instead of the exact call. */
const QUICK = new Set(["/system/daemon/restart"]);
const copyOf = (path: string) => (path.endsWith("/uninstall") ? "uninstall" : "install");
/** Daemon writes the footer asks about in plain words (title, what changes, the verb); the exact call is Expert's. */
const DAEMON = "/system/daemon/";
const PLAIN: Record<string, "pause" | "resume"> = {
  "/system/daemon/pause": "pause",
  "/system/daemon/resume": "resume",
};
/** Writes whose committing button names the action instead of "Run it". */
const ACTION: Record<string, MessageKey> = {
  "/org/calendar-reminders-install": "calendar.setUpWorker",
  "/org/calendar-reminders-uninstall": "calendar.turnOff",
  "/system/daemon/pause": "daemon.pause",
  "/system/daemon/resume": "daemon.resume",
};

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

/** "4 s", "1 min 12 s": how long a step took, or has run so far. */
function duration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return seconds < 60
    ? t("op.seconds", { seconds })
    : t("op.minutes", { minutes: Math.floor(seconds / 60), seconds: seconds % 60 });
}

/** A finished step's time, or a running one's once it has run 3 s; nothing for a step that kept no time. */
function timeOf(phase: Phase & { startedAt?: number; endedAt?: number }, now: number): string | null {
  if (phase.startedAt === undefined) return null;
  if (phase.endedAt !== undefined) return duration(phase.endedAt - phase.startedAt);
  return phase.state === "running" && now - phase.startedAt >= 3000 ? duration(now - phase.startedAt) : null;
}

/** The steps as a segmented bar, one segment per planned step: never a percentage the head did not measure. */
function Steps({
  phases,
  active,
  uncertain = false,
}: Readonly<{ phases: Phase[]; active: boolean; uncertain?: boolean }>) {
  const now = Date.now();
  if (!phases.length) return null;
  const finished = phases.filter((phase) => ["done", "warn", "skipped"].includes(phase.state)).length;
  const running = phases.some((phase) => phase.state === "running");
  return (
    <div className="mt-3 space-y-2">
      <Progress
        label={t("op.steps")}
        names={false}
        pulse={active}
        segments={phases.map((phase) => ({ id: phase.id, label: phase.label, state: phase.state }))}
        valueText={
          uncertain
            ? t("op.lastReportedSteps")
            : t("op.step", {
                done: Math.min(finished + (running ? 1 : 0), phases.length),
                total: phases.length,
              })
        }
      />
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
                  <Icon className={cn("size-4", active && phase.state === "running" && "motion-safe:animate-spin")} />
                ) : (
                  <span className="size-1.5 rounded-full bg-current opacity-50" />
                )}
              </span>
              <span className={cn("min-w-0 flex-1", phase.state === "pending" && "text-muted-foreground")}>
                {phase.label}
                {uncertain ? (
                  <span className="block text-xs text-muted-foreground">
                    {t("op.lastReported", { state: t(`op.state.${phase.state}` as MessageKey) })}
                  </span>
                ) : (
                  <span className="sr-only">: {t(`op.state.${phase.state}` as MessageKey)}</span>
                )}
                {phase.detail && (
                  <span
                    className={cn(
                      "block text-xs",
                      phase.state === "done" ? "text-muted-foreground" : TONE[phase.state],
                    )}
                  >
                    {phase.detail}
                  </span>
                )}
              </span>
              {timeOf(phase, now) && (
                <span aria-hidden className="mt-0.5 shrink-0 text-xs text-muted-foreground tabular-nums">
                  {timeOf(phase, now)}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function titleOf(run: Exclude<OperationRun, { stage: "idle" }>): string {
  if (run.driven && run.stage === "done") return run.driven.doneTitle;
  if (run.driven && run.stage !== "confirm") return run.driven.runningTitle;
  const plain = PLAIN[run.call.path];
  if (plain) return t(`op.daemon.${plain}.title`);
  return PERSISTENT.has(run.call.path) ? t(`op.daemon.${copyOf(run.call.path)}.title` as MessageKey) : run.call.summary;
}

/** "Step 4 of 6: Waiting for hz to come back": what the live region says when a step starts. */
function stepText(phases: Phase[]): string {
  const index = phases.findIndex((phase) => phase.state === "running" || phase.state === "warn");
  const at = index < 0 ? phases.filter((phase) => phase.state !== "pending").length - 1 : index;
  const phase = phases[at];
  return phase ? t("op.stepOf", { done: at + 1, total: phases.length, label: phase.label }) : "";
}

/** Each string in the body that will be sent and that the command shows shortened: built from the body, never from page text. */
function shortenedValues(run: Exclude<OperationRun, { stage: "idle" }>): [string, string][] {
  const body = run.call.body ?? {};
  return Object.entries(body).filter(
    (entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].length > LONG_FORM_VALUE,
  );
}

/** "Show all": every shortened value in full, scrollable and read-only, as it will be sent. */
function ShowAll({ values }: Readonly<{ values: [string, string][] }>) {
  if (!values.length) return null;
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-xs text-muted-foreground">{t("op.showAll")}</summary>
      <p className="mt-1 text-xs text-muted-foreground">{t("op.showAllNote")}</p>
      {values.map(([name, value]) => (
        <label key={name} className="mt-2 block text-xs">
          <span className="font-mono">{name}</span>
          <textarea
            readOnly
            value={value}
            rows={6}
            className="mt-1 block max-h-48 w-full resize-none overflow-y-auto rounded bg-muted px-2 py-1 font-mono text-xs break-all"
          />
        </label>
      ))}
    </details>
  );
}

/** Mount once per place that can start a write; only the first mounted shows the dialog. */
export function OperationDialog() {
  const run = useOperationRun();
  const client = useQueryClient();
  const primary = usePrimaryHost();
  const heading = useId();
  const box = useRef<HTMLElement | null>(null);
  const title = useRef<HTMLParagraphElement>(null);
  const preferredFocus = useRef<HTMLButtonElement>(null);
  const [armedRecovery, setArmedRecovery] = useState<OperationRun | null>(null);
  const stage = run.stage;
  // The exact daemon call is for experts alone. Expert mode as the app already loaded it (the sidebar reads it); the
  // dialog never asks the server itself, so an unknown mode keeps the plain words.
  const expert = useExpert(true).data?.expert === true;
  const driven = run.stage !== "idle" && run.driven !== undefined;
  const pluginInstall = run.stage !== "idle" && run.call.path === "/system/plugin/add";
  const hidden = pluginInstall && isOperationProgressHidden(run);
  const modal = primary && pluginInstall && !hidden;
  const showProgress = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    const element = box.current;
    if (!modal || !(element instanceof HTMLDialogElement)) return;
    element.showModal();
    preferredFocus.current?.focus();
    return () => element.close();
  }, [modal]);
  // Focus is never lost when a stage swaps the buttons: a driven run's progress lands on its title, its outcome on
  // the primary button (autoFocus); any other write's outcome on the dialog, the confirmation on Cancel.
  useEffect(() => {
    if (!primary || hidden) return;
    if (driven && stage === "running") title.current?.focus();
    else if (!driven && (stage === "done" || stage === "failed")) box.current?.focus();
  }, [primary, stage, driven, hidden]);
  useEffect(() => {
    if (primary && hidden) showProgress.current?.focus();
  }, [primary, hidden]);
  const quick = run.stage !== "idle" && QUICK.has(run.call.path);
  const settled = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["ui"], refetchType: "all" }),
      client.invalidateQueries({ queryKey: daemonStatusKey, refetchType: "all" }),
      client.invalidateQueries({ queryKey: ["org", "calendar-reminders-status"], refetchType: "all" }),
    ]).then(() => undefined);
  const go = () => {
    if (getRun() === run) void startOperation(() => void settled());
  };
  const close = () => {
    if (getRun() !== run) return;
    setArmedRecovery(null);
    dismissOperation();
  };
  // A quick write needs no confirmation: asking for it starts it.
  useEffect(() => {
    if (primary && stage === "confirm" && quick) go();
  });
  if (!primary || run.stage === "idle" || (quick && run.stage === "confirm")) return null;
  if (hidden) {
    return createPortal(
      <Button
        ref={showProgress}
        type="button"
        variant="outline"
        className="fixed right-4 bottom-[calc(4.75rem+var(--hz-viewport-safe-bottom,0px))] z-(--layer-modal) shadow-md"
        onClick={() => setOperationProgressHidden(run, false)}
      >
        {t("plugins.showProgress")}
      </Button>,
      document.body,
    );
  }
  const persistent = PERSISTENT.has(run.call.path);
  const copy = copyOf(run.call.path);
  const plain = (kind: "running" | "failed") => t(`op.daemon.restart.${kind}` as MessageKey);
  const restartResult = quick && run.stage === "done" ? daemonRestartResult(run.result) : null;
  const Container = modal ? "dialog" : "div";
  const cancel = run.stage === "running" ? run.cancel : undefined;
  return createPortal(
    <Container
      ref={(node: HTMLElement | null) => {
        box.current = node;
      }}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby={heading}
      data-operation={run.call.path}
      data-tracked={run.stage !== "confirm" && run.tracked ? "" : undefined}
      data-stage={run.stage}
      onCancel={(event) => event.preventDefault()}
      onKeyDown={(event) => {
        // A dialog it covers (the setup wizard) must not close, or move the focus away, because of what is typed here.
        event.stopPropagation();
        if (modal && event.key === "Tab") {
          const controls = Array.from(
            event.currentTarget.querySelectorAll<HTMLElement>(
              'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
            ),
          ).filter((element) => element.getClientRects().length > 0);
          const first = controls[0];
          const last = controls.at(-1);
          if (first && last && (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
            event.preventDefault();
            (event.shiftKey ? last : first).focus();
          }
        }
        if (event.key === "Escape") {
          event.preventDefault();
          if (run.stage !== "running") close();
        }
      }}
      className="confirmation-dialog fixed z-(--layer-modal) max-h-[calc(var(--hz-viewport-height,100dvh)-2rem)] w-[min(28rem,calc(var(--hz-viewport-width,100vw)-2rem))] overflow-y-auto rounded-xl border border-border bg-popover p-4 text-sm text-foreground shadow-xl outline-none backdrop:bg-black/25"
    >
      <div className="flex items-start gap-2">
        <p
          id={heading}
          ref={title}
          tabIndex={-1}
          className="min-w-0 flex-1 text-base font-medium outline-none focus:outline-none focus-visible:outline-none"
        >
          {titleOf(run)}
        </p>
        {driven && run.stage !== "running" && (
          <div data-pane-chrome="" className="flex shrink-0 items-center gap-1.5">
            <PaneAction icon={X} ghost onClick={close}>
              {t("op.close")}
            </PaneAction>
          </div>
        )}
      </div>
      {driven && run.stage !== "confirm" && (
        <output aria-live="polite" aria-atomic="true" className="mt-1 block text-muted-foreground">
          {run.stage === "running" || run.stage === "done" ? stepText(run.phases) : null}
        </output>
      )}
      {run.stage === "confirm" && run.driven && (
        <>
          {run.driven.consent.map((line) => (
            <p key={line} className="mt-1 text-muted-foreground">
              {line}
            </p>
          ))}
          {run.driven.details}
          <PaneActions align="end" words="all">
            <PaneAction key="cancel" ref={preferredFocus} icon={X} autoFocus onClick={close}>
              {t("contrib.cancel")}
            </PaneAction>
            <PaneAction key="go" icon={Play} primary destructive={run.driven.destructive} onClick={go}>
              {run.driven.action}
            </PaneAction>
          </PaneActions>
        </>
      )}
      {run.stage === "confirm" && !run.driven && (
        <>
          <p className="mt-1 text-muted-foreground">
            {persistent
              ? t(`op.daemon.${copy}.consent` as MessageKey)
              : PLAIN[run.call.path]
                ? t(`op.daemon.${PLAIN[run.call.path]}.consent` as MessageKey)
                : t("contrib.confirm")}
          </p>
          {run.call.command && (expert || !run.call.path.startsWith(DAEMON)) && (
            <code className="mt-2 block rounded bg-muted px-2 py-1 font-mono text-xs break-all">
              {run.call.command}
            </code>
          )}
          <ShowAll values={shortenedValues(run)} />
          <PaneActions align="end" words="all">
            <PaneAction key="cancel" ref={preferredFocus} icon={X} autoFocus onClick={close}>
              {t("contrib.cancel")}
            </PaneAction>
            <PaneAction key="go" icon={Play} primary onClick={go}>
              {persistent ? t(`op.daemon.${copy}.button` as MessageKey) : t(ACTION[run.call.path] ?? "contrib.run")}
            </PaneAction>
          </PaneActions>
        </>
      )}
      {run.stage === "running" && (
        <>
          {!driven && (
            <output className="mt-1 block text-muted-foreground">
              {run.task ??
                (quick
                  ? plain("running")
                  : persistent
                    ? t(`op.daemon.${copy}.running` as MessageKey)
                    : t("op.working"))}
            </output>
          )}
          <Steps phases={run.phases} active />
          {!cancel && (
            <p className="mt-3 text-xs text-muted-foreground">{t(modal ? "plugins.installInProgress" : "op.noStop")}</p>
          )}
          <PaneActions align="end" words="all">
            {modal && (
              <PaneAction icon={EyeOff} onClick={() => setOperationProgressHidden(run, true)}>
                {t("plugins.hideProgress")}
              </PaneAction>
            )}
            {cancel && (
              <PaneAction icon={X} onClick={cancel}>
                {t("contrib.cancel")}
              </PaneAction>
            )}
            <PaneAction
              key="working"
              icon={Loader2}
              primary
              disabled
              aria-disabled
              className="[&_svg]:motion-safe:animate-spin"
            >
              {run.driven
                ? run.driven.running
                : quick
                  ? plain("running")
                  : persistent
                    ? t(`op.daemon.${copy}.running` as MessageKey)
                    : t("op.working")}
            </PaneAction>
          </PaneActions>
        </>
      )}
      {run.stage === "done" && (
        <>
          <p
            role="status"
            className={cn(
              "mt-1 flex items-start gap-2",
              restartResult && restartResult !== "daemon.restarted" ? "text-warn" : "text-ok",
            )}
          >
            {restartResult && restartResult !== "daemon.restarted" ? (
              <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
            ) : (
              <Check aria-hidden className="mt-0.5 size-4 shrink-0" />
            )}
            {quick
              ? t(daemonRestartResult(run.result))
              : persistent
                ? t(`op.daemon.${copy}.done` as MessageKey)
                : t("op.done")}
          </p>
          <Steps phases={run.phases} active={false} />
          <PaneActions align="end" words="all">
            <PaneAction key="close" ref={preferredFocus} icon={Check} primary autoFocus onClick={close}>
              {t("op.finish")}
            </PaneAction>
          </PaneActions>
        </>
      )}
      {run.stage === "failed" && (
        <>
          <p role="alert" className="mt-1 flex items-start gap-2 text-bad">
            <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 break-words">
              {quick ? (run.outcomeUnknown ? t("op.daemon.restart.unknown") : plain("failed")) : run.error}
            </span>
          </p>
          <Steps phases={run.phases} active={false} uncertain={run.outcomeUnknown} />
          {run.recovery ? (
            <div className="mt-3 text-xs text-muted-foreground">
              {run.error !== run.recovery.message && <p>{run.recovery.message}</p>}
              <p>{run.recovery.help}</p>
            </div>
          ) : !quick ? (
            <p className="mt-3 text-xs text-muted-foreground">
              {run.phases.some((phase) => phase.state === "failed") ? t("op.failedNext") : t("op.lost")}
            </p>
          ) : null}
          <PaneActions align="end" words="all">
            <PaneAction key="close" icon={X} onClick={close}>
              {t("op.close")}
            </PaneAction>
            {/* A tracked write is retried where it was asked; a driven one checks what the failed attempt left. */}
            {run.recovery ? (
              <PaneAction
                key="recover"
                icon={RotateCw}
                primary
                autoFocus
                onClick={() => {
                  if (armedRecovery === run) {
                    setArmedRecovery(null);
                    recoverOperation(run);
                  } else setArmedRecovery(run);
                }}
              >
                {armedRecovery === run ? run.recovery.confirm : run.recovery.action}
              </PaneAction>
            ) : (
              (!run.tracked || run.driven) &&
              !run.outcomeUnknown && (
                <PaneAction key="retry" icon={RotateCw} primary autoFocus={driven} onClick={go}>
                  {t("op.retry")}
                </PaneAction>
              )
            )}
          </PaneActions>
        </>
      )}
      {run.stage === "attention" && (
        <>
          <p role="status" className="mt-1 flex items-start gap-2 text-warn">
            <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 break-words">{run.error}</span>
          </p>
          <Steps phases={run.phases} active={false} />
          <PaneActions align="end" words="all">
            <PaneAction key="close" icon={X} onClick={close}>
              {t("op.close")}
            </PaneAction>
            <PaneAction key="wait" icon={RotateCw} primary autoFocus onClick={go}>
              {t("op.keepWaiting")}
            </PaneAction>
          </PaneActions>
        </>
      )}
    </Container>,
    document.body,
  );
}
