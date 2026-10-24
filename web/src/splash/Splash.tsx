// The splash's controller (ADR 0046). index.html paints the splash before React loads (#splash); from here on this
// drives it: asks hz until it answers, follows the machine's phase with one line, fades it out when the app is ready,
// shows the fix when hz doesn't answer, and brings it back over the app when hz goes away or restarts.
import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { type MessageKey, t } from "@/i18n";
import { Branding } from "@/layout/Branding";
import { useDark, useSelectedTheme } from "@/layout/ThemeTokens";
import {
  fadeMs,
  GIVE_UP_MS,
  leaveAt,
  type Phase,
  POLL_MS,
  type Problem,
  polling,
  type SplashState,
  visible,
  waiting,
} from "./machine";
import { remember } from "./snapshot";
import { type SplashStore, splash } from "./store";

const LINES: Record<Exclude<Phase, "error">, MessageKey> = {
  starting: "splash.starting",
  connecting: "splash.connecting",
  "signing-in": "splash.signingIn",
  restarting: "splash.restarting",
  reconnecting: "splash.reconnecting",
  ready: "splash.ready",
};

export type ProbeResult = { kind: "ok"; info: unknown } | { kind: "refused" } | { kind: "unreachable" };

/** Ask hz who it is (`/system/info`, with the token): answered, refused the token, or not there. */
export async function probeServer(): Promise<ProbeResult> {
  try {
    const { data, response } = await api.GET("/system/info");
    if (response.status === 401) return { kind: "refused" };
    // 5xx: a proxy in front of a stopped hz (`make web-dev`), or hz failing to answer at all.
    if (response.status >= 500) return { kind: "unreachable" };
    return { kind: "ok", info: data }; // a 403 or 404 is an answer: the screens say what it means
  } catch {
    return { kind: "unreachable" };
  }
}

/** The command that fixes it, to copy: no "run in the terminal" here, the terminal needs the hz that isn't there. */
function Command({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // No clipboard: the command is on screen to type.
    }
  };
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-border bg-muted py-0.5 pr-0.5 pl-2">
      <code className="font-mono text-xs text-foreground">{command}</code>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 px-1.5"
        onClick={copy}
        aria-label={t("copy.aria", { command })}
        title={copied ? t("copy.done") : t("copy.label")}
      >
        {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
        <span className="sr-only" aria-live="polite">
          {copied ? t("copy.done") : ""}
        </span>
      </Button>
    </span>
  );
}

/** The fix for a problem: what happened and the command that helps. */
function Fix({ problem }: { problem: Problem }) {
  const [title, text] =
    problem === "token"
      ? (["splash.tokenTitle", "splash.token"] as const)
      : (["splash.unreachableTitle", "splash.unreachable"] as const);
  return (
    <div role="alert" className="flex max-w-md flex-col items-center gap-3 text-sm">
      <h1 className="text-lg font-semibold text-foreground">{t(title)}</h1>
      <p className="text-muted-foreground">{t(text)}</p>
      <Command command="hz ui" />
    </div>
  );
}

const byId = (id: string) => document.getElementById(id);

/** Show #splash over the app while `shown` (the app can't take focus meanwhile); fade it out, never too early. */
function useSplashElement(state: SplashState, now: () => number) {
  const shown = visible(state);
  const { since } = state;
  // biome-ignore lint/correctness/useExhaustiveDependencies: `since` is read through `state`, the latest one
  useEffect(() => {
    const element = byId("splash");
    const app = byId("root");
    if (!element) return;
    if (shown) {
      element.hidden = false;
      app?.setAttribute("inert", "");
      const frame = requestAnimationFrame(() => element.removeAttribute("data-leaving"));
      return () => cancelAnimationFrame(frame);
    }
    let fade: ReturnType<typeof setTimeout> | undefined;
    const leave = setTimeout(() => {
      element.setAttribute("data-leaving", "");
      app?.removeAttribute("inert");
      fade = setTimeout(() => {
        element.hidden = true;
      }, fadeMs());
    }, leaveAt(state, now()) - now());
    return () => {
      clearTimeout(leave);
      clearTimeout(fade);
    };
  }, [shown, since, now]);
  // The line, and whether the CSS shows the progress or the fix.
  useEffect(() => {
    const element = byId("splash");
    const line = byId("splash-line");
    if (element) element.dataset.state = state.phase === "error" ? "error" : "busy";
    if (line && state.phase !== "error") line.textContent = t(LINES[state.phase]);
  }, [state.phase]);
}

type Props = { store?: SplashStore; probe?: () => Promise<ProbeResult>; now?: () => number };
const clock = () => performance.now();

export function Splash({ store = splash, probe = probeServer, now = clock }: Props) {
  const state = useSyncExternalStore(store.subscribe, store.get);
  const client = useQueryClient();
  const theme = useSelectedTheme();
  const dark = useDark();
  useSplashElement(state, now);
  const asking = polling(state);
  const waits = waiting(state);

  useEffect(() => store.send({ type: "mounted" }), [store]);

  /** Ask hz once and tell the machine; whether to ask again. */
  const ask = useCallback(async (): Promise<boolean> => {
    const answer = await probe();
    if (answer.kind === "ok") {
      const back = store.get().booted && store.get().phase !== "ready";
      const version = (answer.info as { version?: unknown } | undefined)?.version;
      if (answer.info) client.setQueryData(["system", "info"], answer.info);
      if (typeof version === "string") {
        remember({ version });
        const element = byId("splash-version");
        if (element) element.textContent = `v${version}`;
      }
      store.send({ type: "answered" });
      if (back) void client.invalidateQueries(); // hz is back: every screen reads again, where the person was
      return false;
    }
    if (answer.kind === "refused") {
      store.send({ type: "refused" });
      return false;
    }
    store.send({ type: "unreachable", at: now() });
    return polling(store.get());
  }, [probe, store, client, now]);

  // Until hz answered and let this page in, or until it is back: ask every POLL_MS.
  useEffect(() => {
    if (!asking) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const loop = async () => {
      if (stopped) return;
      if (await ask()) {
        if (!stopped) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [asking, ask]);

  // Something suspects hz went away (a call couldn't reach it): ask once; the loop above takes over if it is gone.
  const handled = useRef(0);
  useEffect(() => {
    if (state.checks === handled.current) return;
    handled.current = state.checks;
    if (!asking) void ask();
  }, [state.checks, asking, ask]);

  // Give up after GIVE_UP_MS of waiting: the fix instead of a spinner forever.
  useEffect(() => {
    if (!waits) return;
    const timer = setTimeout(() => store.send({ type: "timeout" }), Math.max(0, state.since + GIVE_UP_MS - now()));
    return () => clearTimeout(timer);
  }, [waits, state.since, store, now]);

  // The theme's branding once the list arrived, drawn by the sidebar's component; until then index.html's.
  const [brand, setBrand] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!theme || brand) return;
    const element = byId("splash-brand");
    element?.replaceChildren();
    setBrand(element);
  }, [theme, brand]);
  const problem = byId("splash-problem");

  return (
    <>
      {brand && createPortal(<Branding theme={theme} dark={dark} />, brand)}
      {problem && state.phase === "error" && state.problem && createPortal(<Fix problem={state.problem} />, problem)}
    </>
  );
}

/** Without a token there's nothing to wait for: the page says how to get one, so the splash goes at once. */
export function SplashGone({ store = splash, now = clock }: Omit<Props, "probe">) {
  const state = useSyncExternalStore(store.subscribe, store.get);
  useSplashElement(state, now);
  useEffect(() => {
    store.send({ type: "answered" });
    store.send({ type: "themed" });
  }, [store]);
  return null;
}
