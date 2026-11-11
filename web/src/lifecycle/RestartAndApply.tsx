import { RotateCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import {
  type ApplyOptions,
  type ApplyPhase,
  type ExpectedSetting,
  type Lifecycle,
  RestartApplyError,
  type RestartObservation,
  restartAndApply,
  resumeRestartAndApply,
} from "./controller";

type Props = {
  target: string;
  changes: string[];
  actionLabel?: string;
  confirmation?: string;
  appliedMessage?: string;
  version?: string;
  settings?: ExpectedSetting[];
  /** Return false when applying the change does not require replacing the running server. */
  preserve?: (signal: AbortSignal) => Promise<boolean | undefined>;
  onApplied?: (running: Lifecycle) => Promise<void> | void;
  /** Save a confirmed change for the next manual launch when this server cannot restart itself. */
  onUnsupported?: () => Promise<void> | void;
  deferredMessage?: string;
  initialObservation?: RestartObservation;
  /** The server kept running without restarting: forget the saved observation, since applying again is safe. */
  onNotRestarted?: () => void;
  acquire?: (resume?: RestartObservation) => (() => void) | null;
  options?: ApplyOptions;
  /** A Cancel for the surface that holds this one: drawn with the apply action as one dialog row, Cancel first. */
  /** The change destroys something: its committing buttons are red. */
  destructive?: boolean;
  cancel?: { label: string; disabled?: boolean; onClick: () => void };
};

/** A shared explicit confirmation and recovery surface. It keeps the page mounted and never reloads user drafts. */
export function RestartAndApply({
  target,
  changes,
  actionLabel,
  confirmation,
  appliedMessage,
  version,
  settings,
  preserve,
  onApplied,
  onUnsupported,
  deferredMessage,
  initialObservation,
  onNotRestarted,
  acquire,
  options,
  cancel,
  destructive = false,
}: Props) {
  const [confirming, setConfirming] = useState(false);
  const [phase, setPhase] = useState<ApplyPhase | null>(null);
  const [error, setError] = useState<Error | null>(() =>
    initialObservation ? new Error(t("lifecycle.restartOutcomeUnknown")) : null,
  );
  const [observation, setObservation] = useState<RestartObservation | undefined>(initialObservation);
  const [pending, setPending] = useState(false);
  const [deferred, setDeferred] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);

  const run = async (resume?: RestartObservation) => {
    if (controller.current) return;
    const release = acquire?.(resume);
    if (release === null) return;
    const active = new AbortController();
    controller.current = active;
    setConfirming(false);
    setError(null);
    setDeferred(false);
    setPending(true);
    const settingsOptions: ApplyOptions = {
      ...options,
      signal: options?.signal ? AbortSignal.any([options.signal, active.signal]) : active.signal,
      onObservation: (next) => {
        options?.onObservation?.(next);
        if (mounted.current) setObservation(next);
      },
      onPhase: (next) => {
        options?.onPhase?.(next);
        if (mounted.current && next !== "applied") setPhase(next);
      },
    };
    try {
      const running = resume
        ? await resumeRestartAndApply(resume, settingsOptions)
        : await restartAndApply({ confirmed: true, version, settings, preserve }, settingsOptions);
      await onApplied?.(running);
      if (mounted.current) {
        setPhase("applied");
        setObservation(undefined);
        setError(null);
      }
    } catch (error) {
      let failure: unknown = error;
      if (error instanceof RestartApplyError && error.kind === "unsupported" && onUnsupported) {
        try {
          await onUnsupported();
          if (mounted.current) {
            setPhase(null);
            setObservation(undefined);
            setDeferred(true);
          }
          return;
        } catch (deferredError) {
          failure = deferredError;
        }
      }
      const notRestarted = failure instanceof RestartApplyError && failure.kind === "stale";
      if (notRestarted) onNotRestarted?.();
      if (mounted.current) {
        setPhase(null);
        setError(failure instanceof Error ? failure : new Error(t("lifecycle.failed")));
        // Check again only helps while a restart may be under way; a server that never restarted needs Apply again.
        setObservation(failure instanceof RestartApplyError && !notRestarted ? failure.observation : undefined);
      }
    } finally {
      controller.current = null;
      release?.();
      if (mounted.current) setPending(false);
    }
  };

  return (
    <div className="space-y-2 text-sm">
      {cancel ? (
        <PaneActions align="end" words="all">
          <PaneAction key="cancel" icon={X} disabled={cancel.disabled} onClick={cancel.onClick}>
            {cancel.label}
          </PaneAction>
          {!deferred && !observation && (
            <PaneAction
              key="apply"
              icon={RotateCw}
              primary
              destructive={destructive}
              disabled={pending}
              onClick={() => setConfirming(true)}
            >
              {actionLabel ?? t("lifecycle.action")}
            </PaneAction>
          )}
        </PaneActions>
      ) : (
        !deferred &&
        !observation && (
          <Button size="sm" variant="outline" disabled={pending} onClick={() => setConfirming(true)}>
            {actionLabel ?? t("lifecycle.action")}
          </Button>
        )
      )}
      {deferred && deferredMessage && <p role="status">{deferredMessage}</p>}
      {confirming && (
        <div
          role="alertdialog"
          aria-label={actionLabel ?? t("lifecycle.targetAction", { target })}
          className="space-y-3 rounded-xl border border-border p-3"
        >
          <p>{confirmation ?? t("lifecycle.question", { target })}</p>
          <ul className="list-inside list-disc">
            {changes.map((change) => (
              <li key={change}>{change}</li>
            ))}
          </ul>
          <p className="text-muted-foreground">{t("lifecycle.pageStays")}</p>
          <PaneActions align="end" words="all">
            <PaneAction key="cancel" icon={X} onClick={() => setConfirming(false)}>
              {t("lifecycle.cancel")}
            </PaneAction>
            <PaneAction key="apply" icon={RotateCw} primary destructive={destructive} onClick={() => void run()}>
              {actionLabel ?? t("lifecycle.targetAction", { target })}
            </PaneAction>
          </PaneActions>
        </div>
      )}
      {phase && !error && (
        <p role="status">{phase === "applied" && appliedMessage ? appliedMessage : t(`lifecycle.phase.${phase}`)}</p>
      )}
      {error && (
        <p role="alert" className="text-bad">
          {error.message}
        </p>
      )}
      {error && observation && (
        <Button size="sm" variant="outline" disabled={pending} onClick={() => void run(observation)}>
          {t("lifecycle.checkAgain")}
        </Button>
      )}
      {pending && (phase === "reconnecting" || phase === "verifying") && (
        <Button size="sm" variant="ghost" onClick={() => controller.current?.abort()}>
          {t("lifecycle.stopChecking")}
        </Button>
      )}
    </div>
  );
}
