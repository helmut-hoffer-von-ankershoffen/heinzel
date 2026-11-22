// Automatic updates (ADR 0116): the server owns the plan; this only shows it. Every window polls the same
// `GET /api/auto-update`, so two windows agree and a reload shows what is left of the countdown.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { useAutoUpdateSetting } from "./AutoUpdateToggle";
import { type AutoUpdateStatus, call, KEY, useAutoUpdateStatus } from "./autoUpdateApi";
import { updateTransport } from "./controller";
import { claimFrontendReload, frontendSkew } from "./frontendSkew";
import { reloadHeld } from "./work";

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
  const statusVersion = status?.version;
  const statusPhase = status?.phase;
  const latestStatus = useRef({ version: statusVersion, phase: statusPhase });
  latestStatus.current = { version: statusVersion, phase: statusPhase };
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const loaded = useRef<string | null>(null);
  const [skewBuild, setSkewBuild] = useState<{ version: string; identity: string } | null>(null);
  // A new version is running, but unsent text or a running turn holds the reload until the composer is idle.
  const [reloadWaiting, setReloadWaiting] = useState(false);
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
  useEffect(() => {
    setSkewBuild(null);
    if (!statusVersion || statusPhase === "applying") return;
    const controller = new AbortController();
    let retry: number | undefined;
    const check = () => {
      void frontendSkew(statusVersion, controller.signal)
        .then((identity) => {
          if (
            identity &&
            !controller.signal.aborted &&
            latestStatus.current.version === statusVersion &&
            latestStatus.current.phase === statusPhase
          )
            setSkewBuild({ version: statusVersion, identity });
        })
        .catch(() => {
          // Older or temporarily unavailable servers never trigger reload; retry without overlapping probes.
          if (!controller.signal.aborted) retry = window.setTimeout(check, 30_000);
        });
    };
    check();
    return () => {
      controller.abort();
      window.clearTimeout(retry);
    };
  }, [statusVersion, statusPhase]);
  useEffect(() => {
    const skew = skewBuild?.version === statusVersion ? skewBuild : null;
    if (!reloadWaiting && (!skew || statusPhase === "applying")) return;
    let cancelled = false,
      inFlight = false,
      finished = false;
    const reload = () => {
      if (cancelled || inFlight || finished || reloadHeld()) return;
      inFlight = true;
      void updateTransport
        .clean()
        .catch(() => undefined)
        .then(() => {
          inFlight = false;
          if (cancelled || reloadHeld()) return;
          if (
            !reloadWaiting &&
            skew &&
            (latestStatus.current.version !== skew.version || latestStatus.current.phase === "applying")
          )
            return;
          if (!reloadWaiting && skew && !claimFrontendReload(skew.identity)) {
            setSkewBuild(null);
            return;
          }
          finished = true;
          updateTransport.reload();
        });
    };
    reload();
    const wait = window.setInterval(reload, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(wait);
    };
  }, [reloadWaiting, skewBuild, statusVersion, statusPhase]);
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
  if (!status) return null;
  if ((reloadWaiting || skewBuild?.version === statusVersion) && reloadHeld())
    return (
      <output className="pointer-events-auto fixed right-4 bottom-[76px] z-[calc(var(--layer-modal,60)_+_10)] block w-[min(24rem,calc(var(--hz-viewport-width,100vw)_-_2rem))] rounded-xl border border-foreground/20 bg-background p-4 text-sm text-foreground shadow-lg">
        <p>{t("autoUpdate.reloadLater")}</p>
      </output>
    );
  const left = secondsLeft(status, query.dataUpdatedAt, now);
  const card =
    "pointer-events-auto fixed right-4 bottom-[76px] z-[calc(var(--layer-modal,60)_+_10)] w-[min(24rem,calc(var(--hz-viewport-width,100vw)_-_2rem))] " +
    "space-y-2 rounded-xl border border-foreground/20 bg-background p-4 text-sm text-foreground shadow-lg";
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
