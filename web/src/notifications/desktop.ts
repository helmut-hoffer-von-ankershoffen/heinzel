// Desktop notifications from this tab (ADR 0041): only after the person turned them on in Settings, which is where the
// browser's permission is asked, never on page load. The choice is per browser, so it lives in localStorage.
import { useSyncExternalStore } from "react";
import { isIOS } from "@/lib/browser";

const KEY = "hz.notifications";
const listeners = new Set<() => void>();

export type DesktopState = "unsupported" | "install" | "off" | "on" | "denied";

function supported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

function chosen(): boolean {
  try {
    return localStorage.getItem(KEY) === "on";
  } catch {
    return false;
  }
}

/** Whether this tab may pop up notices now: supported, turned on here, and permitted by the browser. */
export function desktopState(): DesktopState {
  if (
    isIOS() &&
    !window.matchMedia("(display-mode: standalone)").matches &&
    !(navigator as Navigator & { standalone?: boolean }).standalone
  )
    return "install";
  if (!supported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  return chosen() && Notification.permission === "granted" ? "on" : "off";
}

function changed() {
  for (const listener of listeners) listener();
}

/** Turn desktop notifications on (asking the browser for permission) or off; the state that results. */
export async function setDesktop(on: boolean): Promise<DesktopState> {
  if (on && ["install", "unsupported"].includes(desktopState())) return desktopState();
  try {
    if (on) localStorage.setItem(KEY, "on");
    else localStorage.removeItem(KEY);
  } catch {
    // Storage blocked: the choice lasts until the page reloads, which is all it can.
  }
  if (on && supported() && Notification.permission === "default") await Notification.requestPermission();
  changed();
  return desktopState();
}

export function useDesktopState(): DesktopState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    desktopState,
    () => "unsupported",
  );
}

/** A route of this app from a notice, or `/`: resolved first, then compared, so no spelling leaves this origin. */
export function appRoute(route: string): string {
  if (!route.startsWith("/")) return "/";
  try {
    const url = new URL(route, window.location.origin);
    return url.origin === window.location.origin ? `${url.pathname}${url.search}${url.hash}` : "/";
  } catch {
    return "/";
  }
}
