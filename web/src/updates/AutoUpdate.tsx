// Automatic updates (ADR 0116): the server owns the plan; this only shows it. Every window polls the same
// `GET /api/auto-update`, so two windows agree and a reload shows what is left of the countdown.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { useAutoUpdateSetting } from "./AutoUpdateToggle";
import { type AutoUpdateStatus, call, KEY, useAutoUpdateStatus } from "./autoUpdateApi";
import { updateTransport } from "./controller";
import {
  type Blocker,
  forgetReload,
  isStale,
  prepareReload,
  type ReloadNotice,
  reloadBlocker,
  reloadedFor,
  servedEntrypoints,
  takeReloadNotice,
} from "./staleBuild";
import { pageEntrypoints } from "./webBuild";

/** How often a window asks whether hz serves a newer web build, besides when it comes to the front or back online. */
export const BUILD_CHECK_MS = 60_000;
/** How long the notice after an automatic reload stays. */
export const NOTICE_MS = 10_000;

export type { AutoUpdateStatus } from "./autoUpdateApi";

/** What will be updated, in plain words. */
export function updateSummary(status: AutoUpdateStatus): string {
  const plugins = Object.entries(status.plugins).map(([name, version]) => `${name} ${version}`);
  const parts = [...(status.core ? [`hz ${status.core}`] : []), ...plugins];
  return parts.join(", ");
}

/** Seconds left now, counted down locally between polls from what the server said and when it said it. */
export function secondsLeft(status: AutoUpdateStatus | undefined, receivedAt: number, now: number): number | null {
  if (status?.phase !== "countdown" || status.seconds_left == null) return null;
  return Math.max(0, status.seconds_left - Math.floor(Math.max(0, now - receivedAt) / 1000));
}

export function AutoUpdate() {
  const client = useQueryClient();
  const { on } = useAutoUpdateSetting();
  // A setting cached as "off" can be enabled in another window or the CLI without this window knowing. Keep
  // polling every 2 s, also in the background, so each window can show the server's 30-second warning in time.
  const query = useAutoUpdateStatus(true);
  // A setting change observed by this window asks the server again at once.
  const previous = useRef(on);
  useEffect(() => {
    if (previous.current === on) return;
    previous.current = on;
    void client.invalidateQueries({ queryKey: KEY, exact: true });
  }, [client, on]);
  const status = query.data;
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const loaded = useRef<string | null>(null);
  // A new version is running, but unsent text or a running turn holds the reload until the composer is idle.
  const [reloadWaiting, setReloadWaiting] = useState(false);
  // The newer web build hz serves, when this page noticed one (heinzel-management-uke3).
  const [served, setServed] = useState<string[] | null>(null);
  // A reload already happened for that build and the page still runs another: only a person's reload can help.
  const [stuck, setStuck] = useState(false);
  const [blocker, setBlocker] = useState<Blocker>(null);
  const [notice, setNotice] = useState<ReloadNotice | null>(takeReloadNotice);
  useEffect(() => {
    if (status?.phase !== "countdown") return;
    const tick = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(tick);
  }, [status?.phase]);
  useEffect(() => {
    if (!status) return;
    // The server restarted on a new version: load its app instead of running old code against it.
    if (loaded.current === null) loaded.current = status.version;
    else if (loaded.current !== status.version) {
      loaded.current = status.version;
      setReloadWaiting(true);
    }
  }, [status]);
  // Ask whether hz serves another web build than this page runs: now, every minute, when the window comes to the
  // front or back online, and again when the server answers with another version. A page without a build of its own
  // (a dev server) has nothing to compare, and nothing is asked before the server answers at all.
  const version = status?.version;
  useEffect(() => {
    if (pageEntrypoints().length === 0 || version === undefined) return;
    const controller = new AbortController();
    let asking = false;
    const ask = async () => {
      if (asking) return;
      asking = true;
      try {
        const found = await servedEntrypoints(controller.signal);
        if (controller.signal.aborted || found === null) return;
        if (!isStale(found)) {
          forgetReload();
          setStuck(false);
        } else if (reloadedFor(found)) setStuck(true);
        else {
          setServed(found);
          setReloadWaiting(true);
        }
      } finally {
        asking = false;
      }
    };
    const front = () => {
      if (document.visibilityState === "visible") void ask();
    };
    const again = () => void ask();
    void ask();
    const every = window.setInterval(again, BUILD_CHECK_MS);
    document.addEventListener("visibilitychange", front);
    window.addEventListener("focus", again);
    window.addEventListener("online", again);
    return () => {
      controller.abort();
      window.clearInterval(every);
      document.removeEventListener("visibilitychange", front);
      window.removeEventListener("focus", again);
      window.removeEventListener("online", again);
    };
  }, [version]);
  useEffect(() => {
    if (!reloadWaiting) return;
    const reload = () => {
      const held = reloadBlocker();
      setBlocker(held);
      if (held) return false;
      // Unsent text in a window in the background is saved first and restored after the reload.
      if (!prepareReload(served)) {
        setBlocker("draft");
        return false;
      }
      void updateTransport
        .clean()
        .catch(() => undefined)
        .finally(() => updateTransport.reload());
      return true;
    };
    if (reload()) return;
    const wait = window.setInterval(() => {
      if (reload()) stop();
    }, 1000);
    // Going to the background lifts the hold of unsent text: reload then, not a second later.
    const hidden = () => {
      if (document.visibilityState === "hidden" && reload()) stop();
    };
    document.addEventListener("visibilitychange", hidden);
    function stop() {
      window.clearInterval(wait);
      document.removeEventListener("visibilitychange", hidden);
    }
    return stop;
  }, [reloadWaiting, served]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [notice]);
  const act = async (path: "/postpone" | "/dismiss") => {
    setBusy(true);
    try {
      client.setQueryData(KEY, await call("POST", path));
    } catch {
      await query.refetch();
    } finally {
      setBusy(false);
    }
  };
  const card =
    "pointer-events-auto fixed right-4 bottom-[76px] z-[calc(var(--layer-modal,60)_+_10)] w-[min(24rem,calc(var(--hz-viewport-width,100vw)_-_2rem))] " +
    "space-y-2 rounded-xl border border-foreground/20 bg-background p-4 text-sm text-foreground shadow-lg";
  if (reloadWaiting && blocker)
    return (
      <output className={`block ${card}`} data-hz-reload="held">
        <p>{t(blocker === "screen" ? "autoUpdate.reloadAfterWork" : "autoUpdate.reloadLater")}</p>
      </output>
    );
  if (stuck)
    return (
      <output className={`block ${card}`} data-hz-reload="stuck">
        <p>{t("autoUpdate.reloadStuck")}</p>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            if (prepareReload(null)) updateTransport.reload();
          }}
        >
          {t("autoUpdate.reload")}
        </Button>
      </output>
    );
  if (notice)
    return (
      <output className={`block ${card}`} data-hz-reload="done">
        <p>{t("autoUpdate.reloaded")}</p>
        {notice.draft && <p className="text-muted-foreground">{t("autoUpdate.draftKept")}</p>}
        <Button size="sm" variant="outline" onClick={() => setNotice(null)}>
          {t("autoUpdate.ok")}
        </Button>
      </output>
    );
  if (!status) return null;
  const left = secondsLeft(status, query.dataUpdatedAt, now);
  if (status.phase === "countdown" && left != null)
    return (
      <section role="alertdialog" aria-labelledby="hz-auto-update-title" aria-live="polite" className={card}>
        <h2 id="hz-auto-update-title" className="font-medium">
          {t("autoUpdate.countdown", { seconds: left })}
        </h2>
        <p>{t("autoUpdate.what", { what: updateSummary(status) })}</p>
        <p className="text-muted-foreground">{t("autoUpdate.how")}</p>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void act("/postpone")}>
          {t("autoUpdate.postpone")}
        </Button>
      </section>
    );
  if (status.phase === "applying")
    return (
      <output className={`block ${card}`}>
        <p className="font-medium">{t("autoUpdate.applying")}</p>
        <p className="text-muted-foreground">{t("autoUpdate.reconnect")}</p>
      </output>
    );
  if (status.message)
    return (
      <output className={`block ${card}`}>
        <p>{status.message}</p>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void act("/dismiss")}>
          {t("autoUpdate.ok")}
        </Button>
      </output>
    );
  return null;
}
