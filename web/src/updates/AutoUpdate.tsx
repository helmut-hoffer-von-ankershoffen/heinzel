// Automatic updates (ADR 0116): the server owns the plan; this only shows it. Every window polls the same
// `GET /api/auto-update`, so two windows agree and a reload shows what is left of the countdown.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { useAutoUpdateSetting } from "./AutoUpdateToggle";
import { type AutoUpdateStatus, call, KEY, useAutoUpdateStatus } from "./autoUpdateApi";
import { useUpdateReload } from "./useUpdateReload";

export type { AutoUpdateStatus } from "./autoUpdateApi";
export { BUILD_CHECK_MS } from "./useUpdateReload";

/** Why a due reload waits, in the words of the card. */
const HELD = {
  turn: "autoUpdate.reloadLater",
  draft: "autoUpdate.reloadLater",
  screen: "autoUpdate.reloadAfterWork",
  input: "autoUpdate.reloadAfterInput",
} as const;

/** What will be updated, in plain words. */
export function updateSummary(status: AutoUpdateStatus): string {
  const plugins = Object.entries(status.plugins).map(([name, version]) => `${name} ${version}`);
  const parts = [...(status.core ? [`Heinzel ${status.core}`] : []), ...plugins];
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
  const reload = useUpdateReload(status?.version);
  useEffect(() => {
    if (status?.phase !== "countdown") return;
    const tick = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(tick);
  }, [status?.phase]);
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
  if (reload.held)
    return (
      <output className={`block ${card}`} data-hz-reload="held">
        <p>{t(HELD[reload.held])}</p>
      </output>
    );
  if (reload.stuck)
    return (
      <output className={`block ${card}`} data-hz-reload="stuck">
        <p>{t("autoUpdate.reloadStuck")}</p>
        {reload.refused && <p className="text-muted-foreground">{t(HELD[reload.refused])}</p>}
        <Button size="sm" variant="outline" onClick={reload.reloadNow}>
          {t("autoUpdate.reload")}
        </Button>
      </output>
    );
  if (reload.notice)
    return (
      <output className={`block ${card}`} data-hz-reload="done">
        <p>{t("autoUpdate.reloaded")}</p>
        {reload.notice.kept && <p className="text-muted-foreground">{t("autoUpdate.draftKept")}</p>}
        <Button size="sm" variant="outline" onClick={reload.dismiss}>
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
