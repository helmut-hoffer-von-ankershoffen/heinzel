// A deliberate toggle gesture authorizes one host-projected call; readiness remains a server fact.
// The switch shows the person's choice; one status line says whether it is live (design/README.md, pane microcopy).
import { Check, ChevronRight, Copy, ExternalLink, RefreshCw, RotateCw, Share2 } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { problem, type SchemaOperationCall } from "@/api/client";
import { token } from "@/api/token";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

export type ApplySettings = Record<string, unknown>;

/** The host-projected data contract; compatible with the generated ToggleControl schema. */
export type ContributionToggleControl = {
  label: string;
  checked: boolean;
  requested?: boolean | null;
  enable: { call?: SchemaOperationCall | null };
  disable: { call?: SchemaOperationCall | null };
  url?: string | null;
  sign_in_url?: string | null;
  apply_settings?: ApplySettings;
  status?: string | null;
  problem?: string | null;
  explanation?: string | null;
};

type Props = {
  control: ContributionToggleControl;
  /** Longer provider text for the Details disclosure, e.g. a status item's detail lines; never shown inline. */
  details?: ReactNode;
  /** The surrounding pane's own actions, joined to the URL's Share and Open in its one bottom row. */
  actions?: ReactNode;
  refresh: (signal: AbortSignal) => Promise<ContributionToggleControl | null>;
  applySettings: (expected: ApplySettings, signal: AbortSignal) => Promise<void>;
};

/** Bound a callback even when it ignores cancellation; propagate cancellation to cooperative transports too. */
async function bounded<T>(run: (signal: AbortSignal) => Promise<T>, owner: AbortSignal, timeout = 15_000): Promise<T> {
  owner.throwIfAborted();
  const step = new AbortController();
  const cancel = () => step.abort(owner.reason);
  owner.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => step.abort(new DOMException(t("lifecycle.requestTimeout"), "TimeoutError")), timeout);
  let dispose = () => {};
  const aborted = new Promise<never>((_resolve, reject) => {
    const stop = () => reject(step.signal.reason);
    step.signal.addEventListener("abort", stop, { once: true });
    dispose = () => step.signal.removeEventListener("abort", stop);
  });
  try {
    const task = Promise.resolve().then(() => {
      step.signal.throwIfAborted();
      return run(step.signal);
    });
    return await Promise.race([task, aborted]);
  } finally {
    clearTimeout(timer);
    owner.removeEventListener("abort", cancel);
    dispose();
    step.abort();
  }
}

function actionable(call: SchemaOperationCall | null | undefined): call is SchemaOperationCall {
  return (
    !!call && call.method === "POST" && !call.read_only && /^\/[a-z][a-z0-9_-]*(?:\/[a-z][a-z0-9_-]*)*$/.test(call.path)
  );
}

function publicUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.username || parsed.password) return null;
    if (parsed.protocol === "https:" || (value.startsWith("/") && parsed.origin === window.location.origin))
      return parsed.href;
  } catch {
    // Malformed provider data never becomes a link or clipboard target.
  }
  return null;
}

function authenticatedUrl(control: ContributionToggleControl): string | null {
  const bearer = token();
  if (!control.checked || !bearer || /\s/.test(bearer)) return null;
  if (!control.url || control.sign_in_url !== control.url) return null;
  try {
    const parsed = new URL(control.sign_in_url);
    // The server's exact approved origin is the only place a token fragment may go.
    if (parsed.protocol !== "https:" || parsed.origin !== control.sign_in_url) return null;
    const link = new URL(parsed.origin);
    link.hash = new URLSearchParams({ token: bearer }).toString();
    return link.href;
  } catch {
    return null;
  }
}

function expectations(control: ContributionToggleControl): ApplySettings {
  const value = control.apply_settings ?? {};
  const entries = Object.entries(value);
  if (entries.length > 16 || entries.some(([key]) => !/^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$/.test(key)))
    throw new Error(t("contrib.toggle.unavailable"));
  return value;
}

/** The first sentence of a message for the status line; the rest belongs in Details. */
function firstSentence(message: string): [string, string] {
  const match = /^(.+?[.!?])\s+(\S[\s\S]*)$/.exec(message.trim());
  return match ? [match[1] ?? message, match[2] ?? ""] : [message.trim(), ""];
}

type Gesture = "on" | "off" | "restart";

/** How long "Turning on…" may last before it asks for Try again instead of waiting silently (progress rule). */
export const PENDING_LIMIT_MS = 60_000;

/** One control shared by provider Settings and status popovers; no plugin name or alternate restart path. */
export function ContributionToggle({ control, refresh, applySettings, actions, details }: Props) {
  const labelId = useId();
  const noteId = useId();
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [gesture, setGesture] = useState<Gesture | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A failed gesture: its own short line and Try again; the server's words go to Details.
  const [failure, setFailure] = useState<{ kind: Gesture; message: string } | null>(null);
  const [stalled, setStalled] = useState(false);
  const [shareFailed, setShareFailed] = useState(false);
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      active.current?.abort();
    };
  }, []);

  const pending = gesture !== null;
  // The person's choice: what they switched on stays on while hz finishes it; a live feature is on until it stops.
  const chosen = control.checked || control.requested === true;
  const unfinished = !control.checked && control.requested === true;
  const operation = (chosen ? control.disable : control.enable).call;
  const shown = gesture === "on" ? true : gesture === "off" ? false : chosen;
  const url = control.checked && !pending ? publicUrl(control.url) : null;
  const displayUrl = url ? (authenticatedUrl(control) ?? url) : null;
  const copied = control.checked && !!control.url && copiedUrl === control.url;
  const restartNeeded = unfinished && !control.problem && Object.keys(control.apply_settings ?? {}).length > 0;
  const [errorLine, errorRest] = error ? firstSentence(error) : ["", ""];
  // Waiting with nothing to do (no problem, no restart, no gesture) gets a time limit, then Try again.
  const waiting = unfinished && !pending && !control.problem && !restartNeeded && !failure;
  useEffect(() => {
    if (!waiting) {
      setStalled(false);
      return;
    }
    const timer = setTimeout(() => setStalled(true), PENDING_LIMIT_MS);
    return () => clearTimeout(timer);
  }, [waiting]);

  // Exactly one status line (design/README.md): the gesture in flight, then the provider's own words, then ours.
  const note = pending
    ? gesture === "off"
      ? t("contrib.toggle.turningOff")
      : t("contrib.toggle.turningOn")
    : !actionable(operation)
      ? t("contrib.toggle.unavailable")
      : failure
        ? failure.kind === "off"
          ? t("contrib.toggle.notOff")
          : t("contrib.toggle.notFinished")
        : control.problem
          ? control.problem
          : stalled
            ? t("contrib.toggle.notFinished")
            : restartNeeded
              ? t("contrib.toggle.restartHint")
              : copied
                ? t("copy.done")
                : control.status
                  ? control.status
                  : unfinished
                    ? t("contrib.toggle.turningOn")
                    : null;
  const attention = !pending && (!!failure || !!control.problem || restartNeeded || stalled);
  const explanation = [control.explanation, failure?.message, errorRest].filter(Boolean).join("\n");
  const hasDetails = !!explanation || !!details;

  /** Run one host-built call, reread the provider, then finish any declared restart through the shared flow. */
  const run = async (kind: Gesture, call: SchemaOperationCall | null) => {
    if (active.current || (call && !actionable(call))) return;
    const controller = new AbortController();
    active.current = controller;
    const deadline = setTimeout(
      () => controller.abort(new DOMException(t("lifecycle.requestTimeout"), "TimeoutError")),
      90_000,
    );
    setGesture(kind);
    setError(null);
    setFailure(null);
    let refreshAttempted = false;
    try {
      if (call) {
        const address = new URL(`/api${call.path}`, window.location.origin);
        const json = call.body !== null && call.body !== undefined;
        if (!json) for (const [key, value] of Object.entries(call.query)) address.searchParams.set(key, String(value));
        await bounded(async (signal) => {
          const response = await fetch(
            new Request(address, {
              method: call.method,
              signal,
              headers: {
                Authorization: `Bearer ${token() ?? ""}`,
                ...(json ? { "Content-Type": "application/json" } : {}),
              },
              ...(json ? { body: JSON.stringify(call.body) } : {}),
            }),
          );
          if (!response.ok) throw problem(response.status, await response.json().catch(() => null));
        }, controller.signal);
        controller.signal.throwIfAborted();
      }
      refreshAttempted = true;
      const current = await bounded(refresh, controller.signal);
      controller.signal.throwIfAborted();
      if (current) {
        const expected = expectations(current);
        if (Object.keys(expected).length) {
          await bounded((signal) => applySettings(expected, signal), controller.signal, 60_000);
          controller.signal.throwIfAborted();
          await bounded(refresh, controller.signal);
        }
      }
    } catch (cause) {
      if (mounted.current)
        setFailure({ kind, message: cause instanceof Error ? cause.message : t("contrib.toggle.failed") });
      if (!refreshAttempted && !controller.signal.aborted) await bounded(refresh, controller.signal).catch(() => null);
    } finally {
      clearTimeout(deadline);
      controller.abort();
      active.current = null;
      if (mounted.current) setGesture(null);
    }
  };
  const change = () => void run(chosen ? "off" : "on", operation ?? null);
  // Try again repeats what failed (turning on by default); it never runs a call the provider did not declare.
  const retry = () =>
    failure?.kind === "off"
      ? void run("off", control.disable.call ?? null)
      : failure?.kind === "restart"
        ? void run("restart", null)
        : void run("on", control.enable.call ?? null);
  // Restart runs only the existing restart flow for the settings the provider declared; no new mutation.
  const restart = () => void run("restart", null);
  const retryable =
    !pending &&
    (failure?.kind === "off"
      ? actionable(control.disable.call)
      : actionable(control.enable.call) && (!!failure || !!control.problem || stalled));

  const share = async () => {
    const value = authenticatedUrl(control) ?? publicUrl(control.url);
    if (!control.checked || !value || !navigator.share) return;
    setError(null);
    try {
      await navigator.share({ url: value });
      if (mounted.current) {
        setError(null);
        setShareFailed(false);
      }
    } catch (cause) {
      if (mounted.current) {
        setShareFailed(true);
        if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(t("contrib.toggle.shareFailed"));
      }
    }
  };

  const copy = async () => {
    const value = authenticatedUrl(control) ?? publicUrl(control.url);
    if (!control.checked || !value) return;
    try {
      await navigator.clipboard.writeText(value);
      if (mounted.current) {
        setCopiedUrl(control.url ?? null);
        setError(null);
      }
    } catch {
      if (mounted.current) setError(t("contrib.toggle.copyFailed"));
    }
  };

  return (
    <div className="min-w-0 space-y-2 text-sm">
      <div className="flex items-center justify-between gap-3">
        <span id={labelId} className="min-w-0">
          {control.label}
        </span>
        <span className="ml-auto text-xs text-muted-foreground">
          {pending ? null : shown ? t("contrib.toggle.on") : t("contrib.toggle.off")}
        </span>
        <button
          type="button"
          role="switch"
          aria-labelledby={labelId}
          aria-describedby={noteId}
          aria-checked={shown}
          aria-busy={pending}
          disabled={pending || !actionable(operation)}
          onClick={change}
          className={cn(
            "relative h-7 w-12 shrink-0 rounded-full border-2 border-foreground/40 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50",
            shown ? (attention ? "bg-warn border-warn" : "bg-switch border-switch") : "bg-muted-foreground/30",
          )}
        >
          <span
            aria-hidden
            className={cn(
              "absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow transition-transform",
              shown && "translate-x-5",
            )}
          />
        </button>
      </div>
      <p
        id={noteId}
        role={failure ? "alert" : "status"}
        title={control.explanation ?? undefined}
        className={cn("text-xs", attention ? "text-foreground" : "text-muted-foreground")}
      >
        {note}
      </p>
      {url && <p className="break-all font-mono text-xs">{displayUrl}</p>}
      {error && (
        <p role="alert" className="text-xs text-bad">
          {errorLine}
        </p>
      )}
      {hasDetails && (
        // A native disclosure: content on demand, not an action, so the pane keeps its one action row.
        <details className="group text-xs">
          <summary className="flex w-fit cursor-pointer list-none items-center gap-1 rounded-md text-muted-foreground hover:text-foreground focus-visible:outline-2 [&::-webkit-details-marker]:hidden">
            <ChevronRight aria-hidden className="size-3.5 transition-transform group-open:rotate-90" />
            {t("contrib.toggle.details")}
          </summary>
          <div className="mt-1 space-y-1">
            {explanation && <p className="whitespace-pre-line break-words text-muted-foreground">{explanation}</p>}
            {details}
          </div>
        </details>
      )}
      <PaneActions className="px-0">
        {url && typeof navigator.share === "function" && (
          <PaneAction icon={Share2} onClick={() => void share()}>
            {t("contrib.toggle.shareUrl")}
          </PaneAction>
        )}
        {url && (typeof navigator.share !== "function" || shareFailed) && (
          <PaneAction icon={copied ? Check : Copy} onClick={() => void copy()}>
            {t("contrib.toggle.copyUrl")}
          </PaneAction>
        )}
        {url && (
          <PaneAction icon={ExternalLink} href={displayUrl ?? url}>
            {t("status.open")}
          </PaneAction>
        )}
        {retryable && (
          <PaneAction icon={RefreshCw} onClick={retry}>
            {t("contrib.toggle.retry")}
          </PaneAction>
        )}
        {restartNeeded && !pending && !retryable && (
          <PaneAction icon={RotateCw} onClick={restart}>
            {t("contrib.toggle.restart")}
          </PaneAction>
        )}
        {actions}
      </PaneActions>
    </div>
  );
}
