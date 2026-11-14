// When a window reloads to load an update (ADR 0116, heinzel-management-uke3): the server restarted on another
// version, or hz serves another web build than this page runs, whoever updated it. The reload waits for what
// staleBuild.ts's `reloadBlocker` names, checks once more right before it goes, and never loops; the reloaded page says
// once what happened, when the person sees it.
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { updateTransport } from "./controller";
import {
  autoReloadAllowed,
  type Blocker,
  countAutoReload,
  forgetReload,
  isStale,
  prepareReload,
  reloadBlocker,
  reloadedFor,
  servedEntrypoints,
  takeReloadNotice,
} from "./staleBuild";
import { pageEntrypoints } from "./webBuild";
import { draftRestored, onDraftRestored } from "./work";

/** How often a window asks whether hz serves a newer web build, besides when it comes to the front or back online. */
export const BUILD_CHECK_MS = 60_000;
/** Coming to the front asks again only when the last answer is older than this. */
export const FRONT_CHECK_MS = 10_000;
/** How long the notice after a reload stays once the window is seen; OK closes it sooner. */
export const NOTICE_MS = 10_000;

const visible = () => document.visibilityState === "visible";

export type UpdateReload = {
  /** What holds a reload that is due; null when none is due or nothing holds it. */
  held: Blocker;
  /** The window can't reload by itself (no build change brought it, or it reloaded too often): offer Reload. */
  stuck: boolean;
  /** Why the person's Reload could not go yet; null before they asked. */
  refused: Blocker;
  reloadNow: () => void;
  /** The page came up from an update reload; `kept` once a composer brought its unsent message back. */
  notice: { kept: boolean } | null;
  dismiss: () => void;
};

/** The served build, when it is newer than this page's and a reload is due for it; asks on the triggers above. */
function useServedBuild(version: string | undefined, onStale: (served: string[]) => void, onCurrent: () => void) {
  const last = useRef(0);
  useEffect(() => {
    if (pageEntrypoints().length === 0 || version === undefined) return;
    const controller = new AbortController();
    let asking = false;
    const ask = async () => {
      if (asking) return;
      asking = true;
      try {
        const found = await servedEntrypoints(controller.signal);
        last.current = Date.now();
        if (controller.signal.aborted || found === null) return;
        if (isStale(found)) onStale(found);
        else onCurrent();
      } finally {
        asking = false;
      }
    };
    // Focus and coming to the front fire often (a phone switching apps): not more than once every ten seconds.
    const front = () => {
      if (visible() && Date.now() - last.current >= FRONT_CHECK_MS) void ask();
    };
    const again = () => void ask();
    void ask();
    const every = window.setInterval(again, BUILD_CHECK_MS);
    document.addEventListener("visibilitychange", front);
    window.addEventListener("focus", front);
    window.addEventListener("online", again);
    return () => {
      controller.abort();
      window.clearInterval(every);
      document.removeEventListener("visibilitychange", front);
      window.removeEventListener("focus", front);
      window.removeEventListener("online", again);
    };
  }, [version, onStale, onCurrent]);
}

/** The notice the reload left, shown until the person has seen it: NOTICE_MS after the window is first visible. */
function useReloadNotice() {
  const [shown, setShown] = useState(takeReloadNotice);
  const kept = useSyncExternalStore(onDraftRestored, draftRestored);
  useEffect(() => {
    if (!shown) return;
    let timer: number | undefined;
    const start = () => {
      if (timer === undefined && visible()) timer = window.setTimeout(() => setShown(false), NOTICE_MS);
    };
    start();
    document.addEventListener("visibilitychange", start);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", start);
    };
  }, [shown]);
  return { notice: shown ? { kept } : null, dismiss: () => setShown(false) };
}

/** Reload this window for an update when it is safe; `version` is the one the server reports now. */
export function useUpdateReload(version: string | undefined): UpdateReload {
  const loaded = useRef<string | null>(null);
  // A reload is due: the server runs another version, or serves the build in `served`.
  const [due, setDue] = useState(false);
  const [served, setServed] = useState<string[] | null>(null);
  const servedRef = useRef<string[] | null>(null);
  const [stuck, setStuck] = useState(false);
  const [held, setHeld] = useState<Blocker>(null);
  const [refused, setRefused] = useState<Blocker>(null);
  const { notice, dismiss } = useReloadNotice();
  useEffect(() => {
    if (version === undefined) return;
    // The server restarted on a new version: load its app instead of running old code against it.
    if (loaded.current === null) loaded.current = version;
    else if (loaded.current !== version) {
      loaded.current = version;
      setDue(true);
    }
  }, [version]);
  const onStale = useCallback((found: string[]) => {
    if (reloadedFor(found)) setStuck(true);
    else {
      servedRef.current = found;
      setServed(found);
      setDue(true);
    }
  }, []);
  const onCurrent = useCallback(() => {
    // The page runs what hz serves (again): nothing is due for a build, and a later update may reload it.
    forgetReload();
    setStuck(false);
    if (servedRef.current !== null) {
      servedRef.current = null;
      setServed(null);
      setDue(false);
    }
  }, []);
  useServedBuild(version, onStale, onCurrent);
  useEffect(() => {
    if (!due) return;
    let going = false;
    let stopped = false;
    const attempt = async () => {
      if (going || stopped) return;
      const blocker = reloadBlocker();
      setHeld(blocker);
      if (blocker) return;
      if (!autoReloadAllowed()) {
        setStuck(true);
        setDue(false);
        return;
      }
      going = true;
      try {
        // Right before going: hz may have gone back to the build this page runs (a restart that fell back).
        if (served !== null) {
          const now = await servedEntrypoints();
          if (stopped || now === null) return;
          if (!isStale(now)) return onCurrent();
        }
        if (!prepareReload(served)) return setHeld("input");
        countAutoReload();
        stopped = true;
        await updateTransport.clean().catch(() => undefined);
        updateTransport.reload();
      } finally {
        going = false;
      }
    };
    void attempt();
    const wait = window.setInterval(() => void attempt(), 1000);
    // Going to the background lifts the hold of an unsent chat message: try then, not a second later.
    const hidden = () => {
      if (!visible()) void attempt();
    };
    document.addEventListener("visibilitychange", hidden);
    return () => {
      stopped = true;
      window.clearInterval(wait);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, [due, served, onCurrent]);
  const reloadNow = () => {
    const blocker = reloadBlocker(true);
    if (blocker || !prepareReload(null)) return setRefused(blocker ?? "input");
    updateTransport.reload();
  };
  return { held: due ? held : null, stuck, refused, reloadNow, notice, dismiss };
}
