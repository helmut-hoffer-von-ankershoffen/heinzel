// A deliberate toggle gesture authorizes one host-projected call; readiness remains a server fact.
import { Check, Copy, ExternalLink } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { problem, type SchemaOperationCall } from "@/api/client";
import { sessionToken, token } from "@/api/token";
import { Button } from "@/components/ui/button";
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
};

type Props = {
  control: ContributionToggleControl;
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
  const session = sessionToken();
  if (!control.checked || !session || session.startsWith("hzd_") || /\s/.test(session)) return null;
  if (!control.url || control.sign_in_url !== control.url) return null;
  try {
    const parsed = new URL(control.sign_in_url);
    // The server's exact approved origin is the only place a session fragment may go.
    if (parsed.protocol !== "https:" || parsed.origin !== control.sign_in_url) return null;
    const link = new URL(parsed.origin);
    link.hash = new URLSearchParams({ token: session }).toString();
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

/** One control shared by provider Settings and status popovers; no plugin name or alternate restart path. */
export function ContributionToggle({ control, refresh, applySettings }: Props) {
  const labelId = useId();
  const noteId = useId();
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      active.current?.abort();
    };
  }, []);

  const requested = !control.checked && control.requested === true;
  const operation = (control.checked || requested ? control.disable : control.enable).call;
  const url = control.checked && !pending ? publicUrl(control.url) : null;
  const copied = control.checked && !!control.url && copiedUrl === control.url;

  const change = async () => {
    if (active.current || !actionable(operation)) return;
    const controller = new AbortController();
    active.current = controller;
    const deadline = setTimeout(
      () => controller.abort(new DOMException(t("lifecycle.requestTimeout"), "TimeoutError")),
      90_000,
    );
    setPending(true);
    setError(null);
    let refreshAttempted = false;
    try {
      const address = new URL(`/api${operation.path}`, window.location.origin);
      const json = operation.body !== null && operation.body !== undefined;
      if (!json)
        for (const [key, value] of Object.entries(operation.query)) address.searchParams.set(key, String(value));
      await bounded(async (signal) => {
        const response = await fetch(
          new Request(address, {
            method: operation.method,
            signal,
            headers: {
              Authorization: `Bearer ${token() ?? ""}`,
              ...(json ? { "Content-Type": "application/json" } : {}),
            },
            ...(json ? { body: JSON.stringify(operation.body) } : {}),
          }),
        );
        if (!response.ok) throw problem(response.status, await response.json().catch(() => null));
      }, controller.signal);
      controller.signal.throwIfAborted();
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
      if (mounted.current) setError(cause instanceof Error ? cause.message : t("contrib.toggle.failed"));
      if (!refreshAttempted && !controller.signal.aborted) await bounded(refresh, controller.signal).catch(() => null);
    } finally {
      clearTimeout(deadline);
      controller.abort();
      active.current = null;
      if (mounted.current) setPending(false);
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
        <button
          type="button"
          role="switch"
          aria-labelledby={labelId}
          aria-describedby={noteId}
          aria-checked={control.checked}
          aria-busy={pending}
          disabled={pending || !actionable(operation)}
          onClick={() => void change()}
          className={cn(
            "relative h-6 w-10 shrink-0 rounded-full transition-colors disabled:opacity-50",
            control.checked ? "bg-switch" : requested ? "bg-warn/30" : "bg-accent",
          )}
        >
          <span
            aria-hidden
            className={cn(
              "absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow transition-transform",
              control.checked ? "translate-x-4" : requested && "translate-x-2",
            )}
          />
        </button>
      </div>
      <p id={noteId} role="status" className="text-xs text-muted-foreground">
        {pending
          ? t("contrib.toggle.working")
          : requested
            ? t("contrib.toggle.pending")
            : !actionable(operation)
              ? t("contrib.toggle.unavailable")
              : !control.checked
                ? t("contrib.toggle.notReady")
                : copied
                  ? t("copy.done")
                  : null}
      </p>
      {url && (
        <div className="space-y-2">
          <p className="break-all font-mono text-xs">{control.url}</p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => void copy()}>
              {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
              {t("contrib.toggle.copyUrl")}
            </Button>
            <Button size="sm" variant="outline" asChild>
              <a href={url} target="_blank" rel="noopener noreferrer">
                <ExternalLink aria-hidden />
                {t("status.open")}
              </a>
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-bad">
          {error}
        </p>
      )}
    </div>
  );
}
