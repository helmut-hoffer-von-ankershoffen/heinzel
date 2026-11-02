// Automatic updates (ADR 0116): the server owns the plan; this only shows it. Every window polls the same
// `GET /api/auto-update`, so two windows agree and a reload shows what is left of the countdown.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { useAutoUpdateSetting } from "./AutoUpdateToggle";
import { type AutoUpdateStatus, call, KEY } from "./autoUpdateApi";
import { updateTransport } from "./controller";
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
  // Turning the setting on or off, here or in another place, asks the server again at once.
  const { on } = useAutoUpdateSetting();
  const query = useQuery({
    queryKey: [...KEY, on],
    queryFn: ({ signal }) => call("GET", "", signal),
    // Every 2 s while anything is pending, so a countdown shows within 2 of its 30 seconds; a minute when off.
    refetchInterval: (state) => (state.state.data?.phase === "off" ? 60_000 : 2_000),
    retry: false,
  });
  const status = query.data;
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const loaded = useRef<string | null>(null);
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
    if (!reloadWaiting) return;
    const reload = () => {
      if (reloadHeld()) return false;
      void updateTransport
        .clean()
        .catch(() => undefined)
        .finally(() => updateTransport.reload());
      return true;
    };
    if (reload()) return;
    const wait = window.setInterval(() => {
      if (reload()) window.clearInterval(wait);
    }, 1000);
    return () => window.clearInterval(wait);
  }, [reloadWaiting]);
  const act = async (path: "/postpone" | "/dismiss") => {
    setBusy(true);
    try {
      client.setQueryData([...KEY, on], await call("POST", path));
    } catch {
      await query.refetch();
    } finally {
      setBusy(false);
    }
  };
  if (!status) return null;
  if (reloadWaiting && reloadHeld())
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
