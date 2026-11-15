// Desktop notifications from this tab (ADR 0041): only after the person turned them on in Settings, which is where the
// browser's permission is asked, never on page load. The choice is per browser, so it lives in localStorage.
import { useSyncExternalStore } from "react";
import { isIOS } from "@/lib/browser";

const KEY = "hz.notifications";
const NATIVE_PERMISSION_EVENT = "heinzel-notification-permissions-changed";
const listeners = new Set<() => void>();

export type DesktopState = "unsupported" | "install" | "off" | "on" | "denied";

type NativeNotice = {
  action: "show";
  id: string;
  title: string;
  body: string;
  path: string;
  expires: number | null;
};
type NativeRequest = NativeNotice | { action: "status" | "enable" | "disable" | "take" };
type NativeBridge = (request: NativeRequest) => Promise<unknown>;

let nativeState: DesktopState = "off";
let nativeRevision = 0;
let changingNativeState = false;

export function nativeNotices(): NativeBridge | undefined {
  if (typeof window === "undefined") return;
  const notices = (window as Window & { heinzelDesktop?: { notices?: NativeBridge } }).heinzelDesktop?.notices;
  return typeof notices === "function" ? notices : undefined;
}

async function updateNativeState(notices: NativeBridge, action: "status" | "enable" | "disable") {
  const revision = ++nativeRevision;
  try {
    const result = await notices({ action });
    if (revision === nativeRevision) {
      nativeState = result === "on" || result === "off" || result === "denied" ? result : "unsupported";
      changed();
    }
  } catch {
    if (revision === nativeRevision && action === "status") {
      nativeState = "off";
      changed();
    }
  }
  return nativeState;
}

function refreshNativeState() {
  const notices = nativeNotices();
  if (notices && !changingNativeState && document.visibilityState !== "hidden")
    void updateNativeState(notices, "status");
}

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
  if (nativeNotices()) return nativeState;
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
  const notices = nativeNotices();
  if (notices) {
    if (changingNativeState) return nativeState;
    changingNativeState = true;
    try {
      return await updateNativeState(notices, on ? "enable" : "disable");
    } finally {
      changingNativeState = false;
    }
  }
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

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1 && nativeNotices()) {
    window.addEventListener("focus", refreshNativeState);
    window.addEventListener(NATIVE_PERMISSION_EVENT, refreshNativeState);
    document.addEventListener("visibilitychange", refreshNativeState);
    refreshNativeState();
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      window.removeEventListener("focus", refreshNativeState);
      window.removeEventListener(NATIVE_PERMISSION_EVENT, refreshNativeState);
      document.removeEventListener("visibilitychange", refreshNativeState);
    }
  };
}

export function useDesktopState(): DesktopState {
  return useSyncExternalStore(subscribe, desktopState, () => "unsupported");
}

export function notificationStateKey(state: DesktopState) {
  if (nativeNotices() && (state === "on" || state === "denied" || state === "unsupported")) {
    return `notify.native.${state}` as const;
  }
  return `notify.state.${state}` as const;
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
