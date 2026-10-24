// What the splash shows while the app can't be used yet (ADR 0046): one calm line per phase, and a fix when hz
// doesn't answer. Pure: the controller (Splash.tsx) feeds it events and the time, and draws what it says.

/** The splash stays at least this long, so it never flickers; fades out in `FADE_MS`; gives up after `GIVE_UP_MS`. */
export const MIN_VISIBLE_MS = 300;
export const FADE_MS = 200;
export const GIVE_UP_MS = 8_000;
/** How often an unanswered hz is asked again. */
export const POLL_MS = 1_000;

export type Phase = "starting" | "connecting" | "signing-in" | "restarting" | "reconnecting" | "ready" | "error";
/** Why it gave up: hz doesn't answer, or it refuses this page's token. */
export type Problem = "unreachable" | "token";

export type SplashState = {
  phase: Phase;
  problem: Problem | null;
  /** When the splash appeared, on `performance.now()`'s clock: 0 is the page's first paint. */
  since: number;
  /** hz answered, and accepted the token. */
  reachable: boolean;
  authorized: boolean;
  /** A session token is being exchanged for this browser's device token (ADR 0046). */
  pairing: boolean;
  /** The theme in effect is on the page. */
  themed: boolean;
  /** The app was ready once: losing hz now is a reconnect, not a start. */
  booted: boolean;
  /** Bumped when something suspects hz went away, so the controller asks it. */
  checks: number;
};

export type SplashEvent =
  | { type: "mounted" }
  | { type: "pairing" }
  | { type: "paired" }
  | { type: "answered" }
  | { type: "refused" }
  | { type: "unreachable"; at: number }
  | { type: "themed" }
  | { type: "suspect" }
  | { type: "timeout" }
  | { type: "restart"; at: number }
  | { type: "restart-failed" };

export const initial: SplashState = {
  phase: "starting",
  problem: null,
  since: 0,
  reachable: false,
  authorized: false,
  pairing: false,
  themed: false,
  booted: false,
  checks: 0,
};

/** The phase the facts lead to, outside a restart and a problem. */
function settle(state: SplashState): SplashState {
  if (!state.reachable) return { ...state, phase: state.booted ? "reconnecting" : "connecting" };
  if (!state.authorized || state.pairing || !state.themed) return { ...state, phase: "signing-in" };
  return { ...state, phase: "ready", problem: null, booted: true };
}

export function reduce(state: SplashState, event: SplashEvent): SplashState {
  // A restart ends in a reload (useRestartServer); until then nothing but its failure changes the line.
  if (state.phase === "restarting" && event.type !== "restart-failed") return state;
  switch (event.type) {
    case "mounted":
      return state.phase === "starting" ? settle(state) : state;
    case "pairing":
      return state.phase === "error" ? { ...state, pairing: true } : settle({ ...state, pairing: true });
    case "paired":
      return state.phase === "error" ? { ...state, pairing: false } : settle({ ...state, pairing: false });
    case "themed":
      return state.phase === "error" ? { ...state, themed: true } : settle({ ...state, themed: true });
    case "answered":
      // hz is back, or let this page in at last: whatever was wrong is over.
      return settle({ ...state, reachable: true, authorized: true, problem: null });
    case "refused":
      return { ...state, reachable: true, authorized: false, phase: "error", problem: "token" };
    case "unreachable": {
      if (state.phase === "error") return { ...state, reachable: false, problem: "unreachable" };
      const next = settle({ ...state, reachable: false });
      // The overlay appears now: it counts its least time and its patience from here.
      return state.phase === "ready" ? { ...next, since: event.at } : next;
    }
    case "suspect":
      return state.phase === "error" ? state : { ...state, checks: state.checks + 1 };
    case "timeout":
      if (state.phase === "ready" || state.phase === "error") return state;
      // Nothing answered: say how to start hz. It answered but something else is slow: stop blocking the app.
      if (!state.reachable) return { ...state, phase: "error", problem: "unreachable" };
      return { ...state, phase: "ready", problem: null, booted: true };
    case "restart":
      return { ...state, phase: "restarting", problem: null, since: event.at };
    case "restart-failed":
      return { ...state, phase: "error", problem: "unreachable", reachable: false };
  }
}

/** Whether the splash covers the app. */
export const visible = (state: SplashState): boolean => state.phase !== "ready";

/** Whether the controller asks hz again and again: until it answered and accepted the token, or it is back. */
export const polling = (state: SplashState): boolean =>
  state.phase === "connecting" ||
  state.phase === "reconnecting" ||
  (state.phase === "signing-in" && !state.authorized) ||
  (state.phase === "error" && state.problem === "unreachable");

/** Whether the give-up clock runs: while waiting for hz, not once it showed the fix or a restart is under way. */
export const waiting = (state: SplashState): boolean =>
  state.phase === "connecting" || state.phase === "signing-in" || state.phase === "reconnecting";

/** When a splash that became ready may start fading: never before `MIN_VISIBLE_MS` after it appeared. */
export const leaveAt = (state: SplashState, now: number): number => Math.max(now, state.since + MIN_VISIBLE_MS);

/** The fade, or none for someone who asked the system for less motion. */
export function fadeMs(
  media: ((query: string) => { matches: boolean }) | undefined = window.matchMedia
    ? (query) => window.matchMedia(query)
    : undefined,
): number {
  try {
    return media?.("(prefers-reduced-motion: reduce)").matches ? 0 : FADE_MS;
  } catch {
    return FADE_MS;
  }
}
