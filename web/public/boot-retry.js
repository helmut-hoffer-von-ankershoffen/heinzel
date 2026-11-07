// Keeps a page that cannot reach hz from staying on a dead splash (ADR 0046). A classic script of this origin, so the
// CSP admits it and it runs even when the app's own bundle failed to load. Two modes, set by `data-mode`:
//   offline: /offline.html, the service worker's answer while hz is down; it reloads the page once hz answers.
//   watch:   the app's index.html; if the app has not mounted after WATCH_MS it behaves like the offline page.
// The probe is the unauthenticated manifest, fetched past the service worker (`hz-probe`), so a cached copy never
// counts as hz being back. Retries back off exponentially to a bound and never stop: a window left open for hours
// still finds hz when it returns.
(() => {
  const BASE_MS = 1_000;
  const MAX_MS = 30_000;
  const WATCH_MS = 15_000;
  const PROBE_TIMEOUT_MS = 5_000;
  const RELOAD_KEY = "hz.boot-reloads";
  const RELOAD_WINDOW_MS = 5 * 60_000;
  const RELOAD_LIMIT = 3;

  /** Wait after the `attempt`th failed probe (1 is the first): 1 s, 2 s, 4 s, ... up to 30 s. */
  function delayFor(attempt) {
    return Math.min(MAX_MS, BASE_MS * 2 ** Math.max(0, attempt - 1));
  }

  /** The words for the status line; nothing in them is version- or command-specific beyond the current command. */
  function statusFor(attempt, delay) {
    const seconds = Math.round(delay / 1000);
    return `hz isn't answering. Trying again in ${seconds} ${seconds === 1 ? "second" : "seconds"} (try ${attempt}).`;
  }

  /** Whether hz answered: the manifest, not the app's page that every unknown path gets. */
  async function probe(fetcher = globalThis.fetch, now = Date.now) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
    try {
      const response = await fetcher(`/manifest.webmanifest?hz-probe=${now()}`, {
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal,
      });
      return response.ok && (response.headers.get("content-type") ?? "").includes("manifest");
    } catch {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  /** Whether another reload is allowed: a page that reloads into the same dead state must not loop. */
  function mayReload(storage, now = Date.now()) {
    try {
      const recent = JSON.parse(storage.getItem(RELOAD_KEY) ?? "[]").filter((at) => now - at < RELOAD_WINDOW_MS);
      if (recent.length >= RELOAD_LIMIT) return false;
      storage.setItem(RELOAD_KEY, JSON.stringify([...recent, now]));
      return true;
    } catch {
      return true; // no storage: reloading once hz answers is still right
    }
  }

  /**
   * Probe until hz answers, then `reload`. `say` receives the status line; `schedule` is setTimeout, replaceable.
   * Returns a stop function.
   */
  function retry({ probe: ask, say, reload, schedule = setTimeout, cancel = clearTimeout }) {
    let attempt = 0;
    let timer;
    let stopped = false;
    let busy = false;
    const step = async () => {
      if (stopped || busy) return;
      busy = true;
      const up = await ask();
      busy = false;
      if (stopped) return;
      if (up) {
        say("hz answered. Opening…");
        reload();
        return;
      }
      attempt += 1;
      const delay = delayFor(attempt);
      say(statusFor(attempt, delay));
      timer = schedule(step, delay);
    };
    const now = () => {
      cancel(timer);
      void step();
    };
    void step();
    return {
      stop: () => {
        stopped = true;
        cancel(timer);
      },
      now,
    };
  }

  function start(mode) {
    const status = document.getElementById("boot-status") ?? document.getElementById("splash-line");
    const say = (text) => {
      if (status) status.textContent = text;
    };
    const reload = () => {
      if (mode === "watch" && !mayReload(globalThis.sessionStorage)) return;
      globalThis.location.reload();
    };
    const engage = () => {
      // Whatever version the last start showed is not the version that will answer.
      const version = document.getElementById("splash-version");
      if (version) version.textContent = "";
      const running = retry({ probe, say, reload });
      for (const [target, name] of [
        [globalThis, "online"],
        [globalThis, "focus"],
        [document, "visibilitychange"],
      ]) {
        target.addEventListener(name, () => {
          if (name !== "visibilitychange" || document.visibilityState === "visible") running.now();
        });
      }
      // A new worker took over: the page it serves may be newer than this one.
      navigator.serviceWorker?.addEventListener("controllerchange", reload);
    };
    if (mode === "offline") {
      engage();
      return;
    }
    setTimeout(() => {
      const root = document.getElementById("root");
      if (root && root.childElementCount === 0) engage();
    }, WATCH_MS);
  }

  const api = { delayFor, statusFor, probe, mayReload, retry, start };
  globalThis.hzBootRetry = api;
  const mode = document.currentScript?.dataset.mode;
  if (mode === "offline" || mode === "watch") start(mode);
})();
