import { useEffect, useState } from "react";
import type { UpdateStatus } from "./controller";
import { reloadHeld, workOnScreen } from "./work";

const KEY = "hz.update.desktopCountdown";
const COUNTDOWN_MS = 30_000;
type Decision = { target: string; deadline: number | null };
let inThisWindow: Decision | null = null;

export function localMacClient(): string | null {
  const bridge = (
    window as Window & { heinzelDesktop?: { clientFlavor?: string; clientOS?: string; remoteWorkspace?: boolean } }
  ).heinzelDesktop;
  const local = ["127.0.0.1", "localhost", "[::1]"].includes(window.location.hostname);
  if (!local || !bridge || bridge.remoteWorkspace) return null;
  // The Electron bundle being replaced may predate clientOS.
  if (bridge.clientFlavor === "electron") return /^Mac/.test(navigator.platform) ? "electron" : null;
  return bridge.clientFlavor === "native" && bridge.clientOS === "macOS" ? "native" : null;
}

export function desktopRestartTarget(status: UpdateStatus | undefined): string | null {
  if (!status) return null;
  const desktop = status.desktop;
  return desktop?.behind && desktop.version && desktop.variant && desktop.variant === localMacClient()
    ? `${desktop.variant}:${desktop.version}:${status.current}`
    : null;
}

function read(target: string): Decision | null {
  try {
    const raw = localStorage.getItem(KEY);
    const saved = raw ? (JSON.parse(raw) as Decision | null) : inThisWindow;
    return saved?.target === target && (saved.deadline === null || Number.isFinite(saved.deadline)) ? saved : null;
  } catch {
    return inThisWindow?.target === target ? inThisWindow : null;
  }
}

function store(target: string, deadline: number | null): void {
  inThisWindow = { target, deadline };
  try {
    localStorage.setItem(KEY, JSON.stringify(inThisWindow));
  } catch {
    // The decision still applies for the lifetime of this window.
  }
}

/** One countdown per app/backend pair, shared by same-origin windows and preserved across frontend reloads. */
export function useDesktopCountdown(target: string | null, enabled: boolean, idle: boolean, restart: () => void) {
  const [seconds, setSeconds] = useState<number | null>(null);
  const [manual, setManual] = useState(false);
  useEffect(() => {
    setSeconds(null);
    setManual(false);
    if (!target) return;
    const update = () => {
      const decision = read(target);
      setManual(decision?.deadline === null);
      if (!enabled || decision?.deadline === null) {
        setSeconds(null);
        return;
      }
      const now = Date.now();
      if (!idle || reloadHeld() || workOnScreen()) {
        if (decision) store(target, now + COUNTDOWN_MS);
        setSeconds(null);
        return;
      }
      const deadline = decision?.deadline ?? now + COUNTDOWN_MS;
      if (!decision) store(target, deadline);
      const left = Math.max(0, Math.ceil((deadline - now) / 1000));
      if (left) {
        setSeconds(left);
        return;
      }
      const attempt = () => {
        if (read(target)?.deadline !== deadline) return;
        if (reloadHeld() || workOnScreen()) return;
        store(target, null);
        setSeconds(null);
        setManual(true);
        restart();
      };
      if (navigator.locks) void navigator.locks.request(KEY, { ifAvailable: true }, (lock) => lock && attempt());
      else attempt();
    };
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [enabled, idle, restart, target]);
  return {
    seconds,
    manual,
    cancel: () => {
      if (!target) return;
      store(target, null);
      setSeconds(null);
      setManual(true);
    },
  };
}
