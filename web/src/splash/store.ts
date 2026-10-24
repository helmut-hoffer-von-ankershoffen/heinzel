// The splash's one state, outside React: main.tsx, the query cache, the theme and a restart all report to it before or
// beside the controller that draws it (Splash.tsx).
import { initial, reduce, type SplashEvent, type SplashState } from "./machine";

export type SplashStore = {
  get: () => SplashState;
  send: (event: SplashEvent) => void;
  subscribe: (listener: () => void) => () => void;
  /** Back to the start, for tests that share the page's store. */
  reset: () => void;
};

export function createSplashStore(start: SplashState = initial): SplashStore {
  let state = start;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    send: (event) => {
      const next = reduce(state, event);
      if (next === state) return;
      state = next;
      for (const listener of listeners) listener();
    },
    reset: () => {
      state = start;
      for (const listener of listeners) listener();
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** The page's splash. */
export const splash = createSplashStore();

/** Whether a failed call means hz may be gone: the network failed, or a proxy in front of it (`make web-dev`) did. */
export function unreachableError(error: unknown): boolean {
  if (error instanceof TypeError) return true;
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" && status >= 502 && status <= 504;
}
