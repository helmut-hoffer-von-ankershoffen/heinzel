// The splash's controller (ADR 0046). index.html paints the splash before React loads (#splash); from here on this
// drives it: asks hz until it answers, follows the machine's phase with one line, fades it out when the app is ready,
// shows the fix when hz doesn't answer, and brings it back over the app when hz goes away or restarts.
import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { api } from "@/api/client";
import { usePlayful } from "@/api/queries";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { Button } from "@/components/ui/button";
import { CodeInput } from "@/components/ui/code-input";
import { type MessageKey, t } from "@/i18n";
import { Branding } from "@/layout/Branding";
import { useDark, useSelectedTheme } from "@/layout/ThemeTokens";
import { installState } from "@/pwa/install";
import {
  fadeMs,
  GIVE_UP_MS,
  leaveAt,
  type Phase,
  type Problem,
  polling,
  retryDelay,
  type SplashState,
  visible,
  waiting,
} from "./machine";
import { remember } from "./snapshot";
import { type SplashStore, splash, unreachableError } from "./store";

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
    // A proxy's gateway error: the hz behind it (`make web-dev`) is stopped. Any other status is hz answering, a 500
    // too: the screens say what it means.
    if (unreachableError({ status: response.status })) return { kind: "unreachable" };
    return { kind: "ok", info: response.ok ? data : undefined };
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

/** What starts the server this page talks to: `hz ui`, or `make web-dev` while developing the app. */
export const START = import.meta.env.DEV ? "make web-dev" : "hz ui";

/**
 * The link `hz ui` printed, pasted: an installed app has no address bar to open it in. Only this page's own address
 * signs in (the token goes nowhere else); the page then reloads with it, as a `#token=…` link would.
 */
export function signInFromLink(
  value: string,
  here: Location = window.location,
): { ok: true; hash: string } | { ok: false; reason: "invalid" | "elsewhere" } {
  let link: URL;
  try {
    link = new URL(value.trim());
  } catch {
    return { ok: false, reason: "invalid" };
  }
  const token = new URLSearchParams(link.hash.replace(/^#/, "")).get("token");
  if (!token) return { ok: false, reason: "invalid" };
  if (link.origin !== here.origin) return { ok: false, reason: "elsewhere" };
  return { ok: true, hash: `#token=${encodeURIComponent(token)}` };
}

function PasteLink() {
  const [value, setValue] = useState("");
  const [reason, setReason] = useState<"invalid" | "elsewhere" | null>(null);
  return (
    <form
      className="flex w-full flex-col gap-2 text-left"
      onSubmit={(event) => {
        event.preventDefault();
        const result = signInFromLink(value);
        if (result.ok) {
          // The app's own hashchange handler adopts the token and reloads.
          window.location.hash = result.hash;
          return;
        }
        setReason(result.reason);
      }}
    >
      <label htmlFor="splash-paste" className="text-xs text-muted-foreground">
        {t("splash.pasteLabel")}
      </label>
      <div className="flex gap-2">
        <CodeInput
          id="splash-paste"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="http://127.0.0.1:…/#token=…"
          aria-describedby={reason ? "splash-paste-problem" : undefined}
        />
        <Button type="submit" size="sm" disabled={!value.trim()}>
          {t("splash.pasteSubmit")}
        </Button>
      </div>
      {reason && (
        <p id="splash-paste-problem" role="alert" className="text-xs text-bad">
          {t(reason === "invalid" ? "splash.pasteInvalid" : "splash.pasteElsewhere", {
            address: window.location.origin,
          })}
        </p>
      )}
    </form>
  );
}

/** The fix for a problem: what happened, what to do, and the command that does it. */
function Fix({ problem }: { problem: Problem }) {
  const installed = installState() === "installed";
  const [title, text, lead] =
    problem === "token"
      ? ([
          "splash.tokenTitle",
          installed ? "splash.tokenApp" : "splash.token",
          installed ? "splash.tokenAppCommand" : "splash.tokenCommand",
        ] as const)
      : (["splash.unreachableTitle", "splash.unreachable", "splash.unreachableCommand"] as const);
  return (
    <div role="alert" className="flex max-w-md flex-col items-center gap-3 text-sm">
      {problem === "unreachable" && <SleepingUnderTree />}
      <h1 className="text-lg font-semibold text-foreground">{t(title)}</h1>
      <p className="text-muted-foreground">{t(text, { command: START })}</p>
      <p className="text-muted-foreground">{t(lead, { command: START })}</p>
      <Command command={START} />
      {problem === "token" && <PasteLink />}
      {problem === "unreachable" && <p className="text-xs text-muted-foreground">{t("splash.retrying")}</p>}
    </div>
  );
}

/** The server is taking a nap; keep the existing recovery steps visible below the illustration. */
function SleepingUnderTree() {
  const playful = usePlayful();
  if (!playful) return null;
  return (
    <div aria-hidden="true" data-testid="offline-scene" className="relative flex h-24 w-44 items-end justify-center">
      <svg
        aria-hidden="true"
        viewBox="0 0 176 96"
        className="absolute inset-0 size-full text-emerald-700/70 dark:text-emerald-300/70"
      >
        <path
          d="M83 14v74M83 30c-19-22-45-16-51 1 15-4 28 2 38 12M83 26c14-22 39-22 48-5-15-1-28 6-39 17"
          fill="none"
          stroke="currentColor"
          strokeWidth="5"
          strokeLinecap="round"
        />
        <path
          d="M83 84c-18-8-39-5-54 2m54-2c23-8 46-4 64 2"
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
        />
        <path d="M66 86h48" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
      </svg>
      <HeinzelAvatar state="sleeping" age="young" shirt="teal" className="relative z-10 h-[4.5rem] w-11" />
    </div>
  );
}

/** A tiny search scene shown while the splash checks whether hz has returned. */
function SearchingScene() {
  const playful = usePlayful();
  if (!playful) return null;
  return (
    <div
      aria-hidden="true"
      data-testid="connection-scene"
      className="relative flex h-20 w-20 items-center justify-center"
    >
      <HeinzelAvatar state="thinking" age="young" shirt="teal" className="h-16 w-10" />
      <svg
        aria-hidden="true"
        viewBox="0 0 40 40"
        className="splash-search-glass absolute right-0 top-3 size-8 text-primary"
      >
        <circle cx="16" cy="16" r="10" fill="none" stroke="currentColor" strokeWidth="3" />
        <path d="m23 23 11 11" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
      </svg>
    </div>
  );
}

/** The line for a phase; "Signing you in…" only while the token is checked or this browser pairs. */
function line(state: SplashState): string {
  if (state.phase === "error") return "";
  if (state.phase === "signing-in" && state.authorized && !state.pairing) return t("splash.opening");
  return t(LINES[state.phase]);
}

const byId = (id: string) => document.getElementById(id);
/** How long a suspected outage waits before hz is asked a second time. */
const CONFIRM_MS = 500;

/** Show #splash over the app while `shown` (the app can't take focus meanwhile); fade it out, never too early. */
function useSplashElement(state: SplashState, now: () => number) {
  const shown = visible(state);
  const { since } = state;
  // Where focus was when the splash came over the app (`inert` drops it), so it goes back there.
  const focused = useRef<HTMLElement | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: `since` is read through `state`, the latest one
  useEffect(() => {
    const element = byId("splash");
    const app = byId("root");
    if (!element) return;
    if (shown) {
      element.hidden = false;
      const active = document.activeElement;
      if (active instanceof HTMLElement && active !== document.body && app?.contains(active)) focused.current = active;
      app?.setAttribute("inert", "");
      const frame = requestAnimationFrame(() => element.removeAttribute("data-leaving"));
      return () => cancelAnimationFrame(frame);
    }
    let fade: ReturnType<typeof setTimeout> | undefined;
    const leave = setTimeout(() => {
      element.setAttribute("data-leaving", "");
      app?.removeAttribute("inert");
      if (focused.current?.isConnected) focused.current.focus({ preventScroll: true });
      focused.current = null;
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
    const text = byId("splash-line");
    if (element) element.dataset.state = state.phase === "error" ? "error" : "busy";
    if (text && state.phase !== "error") text.textContent = line(state);
  }, [state]);
}

type Props = { store?: SplashStore; probe?: () => Promise<ProbeResult>; now?: () => number };
const clock = () => performance.now();

export function Splash({ store = splash, probe = probeServer, now = clock }: Props) {
  const state = useSyncExternalStore(store.subscribe, store.get);
  const client = useQueryClient();
  const theme = useSelectedTheme();
  const dark = useDark();
  const connecting = state.phase === "connecting" || state.phase === "reconnecting";
  useSplashElement(state, now);
  const asking = polling(state);
  const waits = waiting(state);

  useEffect(() => store.send({ type: "mounted" }), [store]);

  /** Tell the machine what hz answered; whether to ask again. */
  const apply = useCallback(
    (answer: ProbeResult): boolean => {
      if (answer.kind === "ok") {
        // hz answers after it didn't (before the app booted, too): every screen reads again, where the person was.
        const back = store.get().lost;
        const version = (answer.info as { version?: unknown } | undefined)?.version;
        if (answer.info) client.setQueryData(["system", "info"], answer.info);
        if (typeof version === "string") {
          remember({ version });
          const element = byId("splash-version");
          if (element) element.textContent = `v${version}`;
        }
        store.send({ type: "answered" });
        if (back) void client.invalidateQueries();
        return false;
      }
      if (answer.kind === "refused") {
        store.send({ type: "refused" });
        return false;
      }
      store.send({ type: "unreachable", at: now() });
      return polling(store.get());
    },
    [store, client, now],
  );

  // Answers apply in the order the questions went out: a late "not there" never undoes a newer answer.
  const asked = useRef(0);
  const applied = useRef(0);
  /** Ask hz once and tell the machine; whether to ask again. `confirm` asks twice before calling hz gone. */
  const ask = useCallback(
    async (confirm = false): Promise<boolean> => {
      const number = ++asked.current;
      let answer = await probe();
      if (confirm && answer.kind === "unreachable") {
        await new Promise((resolve) => setTimeout(resolve, CONFIRM_MS));
        answer = await probe();
      }
      if (number < applied.current) return false;
      applied.current = number;
      return apply(answer);
    },
    [probe, apply],
  );

  // Until hz answered and let this page in, or until it is back: ask every POLL_MS.
  useEffect(() => {
    if (!asking) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let misses = 0;
    const loop = async () => {
      if (stopped) return;
      if (await ask()) {
        if (!stopped) timer = setTimeout(loop, retryDelay(++misses));
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
    if (!asking) void ask(true); // a blip (a laptop waking up) doesn't bring the overlay back
  }, [state.checks, asking, ask]);

  // Give up after GIVE_UP_MS of waiting: the fix instead of a spinner forever.
  useEffect(() => {
    if (!waits || !state.asked) return; // a page that took long to load hasn't waited for hz yet
    const timer = setTimeout(() => store.send({ type: "timeout" }), Math.max(0, state.since + GIVE_UP_MS - now()));
    return () => clearTimeout(timer);
  }, [waits, state.since, state.asked, store, now]);

  // A theme's logo once the list arrived, drawn by the sidebar's component; the built-in theme keeps index.html's
  // brand, which shows the app's icon file (/favicon.svg), so the splash doesn't switch artwork while it is up.
  const [brand, setBrand] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!theme?.logo || theme.name === "default" || brand) return;
    const element = byId("splash-brand");
    element?.replaceChildren();
    setBrand(element);
  }, [theme, brand]);
  const problem = byId("splash-problem");

  return (
    <>
      {brand && createPortal(<Branding theme={theme} dark={dark} />, brand)}
      {problem && state.phase === "error" && state.problem && createPortal(<Fix problem={state.problem} />, problem)}
      {connecting && byId("splash-art") && createPortal(<SearchingScene />, byId("splash-art") as HTMLElement)}
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
