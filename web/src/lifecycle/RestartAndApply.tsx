import { useEffect, useRef, useState } from "react";
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
  version?: string;
  settings?: ExpectedSetting[];
  /** Return false when applying the change does not require replacing the running server. */
  preserve?: (signal: AbortSignal) => Promise<boolean | undefined>;
  onApplied?: (running: Lifecycle) => Promise<void> | void;
  /** Save a confirmed change for the next manual launch when this server cannot restart itself. */
  onUnsupported?: () => Promise<void> | void;
  deferredMessage?: string;
  initialObservation?: RestartObservation;
  options?: ApplyOptions;
};

/** A shared explicit confirmation and recovery surface. It keeps the page mounted and never reloads user drafts. */
export function RestartAndApply({
  target,
  changes,
  version,
  settings,
  preserve,
  onApplied,
  onUnsupported,
  deferredMessage,
  initialObservation,
  options,
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
      if (mounted.current) {
        setError(failure instanceof Error ? failure : new Error(t("lifecycle.failed")));
        setObservation(failure instanceof RestartApplyError ? failure.observation : undefined);
      }
    } finally {
      controller.current = null;
      if (mounted.current) setPending(false);
    }
  };

  return (
    <div className="space-y-2 text-sm">
      {!deferred && !observation && (
        <Button size="sm" variant="outline" disabled={pending} onClick={() => setConfirming(true)}>
          {t("lifecycle.action")}
        </Button>
      )}
      {deferred && deferredMessage && <p role="status">{deferredMessage}</p>}
      {confirming && (
        <div
          role="alertdialog"
          aria-label={t("lifecycle.targetAction", { target })}
          className="space-y-3 rounded-xl border border-border p-3"
        >
          <p>{t("lifecycle.question", { target })}</p>
          <ul className="list-inside list-disc">
            {changes.map((change) => (
              <li key={change}>{change}</li>
            ))}
          </ul>
          <p className="text-muted-foreground">{t("lifecycle.pageStays")}</p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => void run()}>
              {t("lifecycle.targetAction", { target })}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              {t("lifecycle.cancel")}
            </Button>
          </div>
        </div>
      )}
      {phase && !error && <p role="status">{t(`lifecycle.phase.${phase}`)}</p>}
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
