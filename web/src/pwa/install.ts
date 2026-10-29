// Installing the app (ADR 0046): keep Chrome/Edge's prompt until asked; Safari gets Add to Dock on desktop or Add to
// Home Screen on iPhone/iPad. Both display-mode and navigator.standalone can identify an installed app.
import { useSyncExternalStore } from "react";
import { isIOS } from "@/lib/browser";

type InstallPrompt = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> };

let deferred: InstallPrompt | null = null;
const listeners = new Set<() => void>();
const changed = () => {
  for (const listener of listeners) listener();
};

/** Keep the browser's install prompt for later; call once, early, before it fires. */
export function captureInstallPrompt(target: Window = window) {
  target.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferred = event as InstallPrompt;
    changed();
  });
  target.addEventListener("appinstalled", () => {
    deferred = null;
    changed();
  });
}

export type InstallState = "installed" | "prompt" | "safari" | "ios" | "unsupported";

export function installState(): InstallState {
  if (
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone
  )
    return "installed";
  if (deferred) return "prompt";
  if (isIOS()) return "ios";
  const agent = navigator.userAgent;
  if (/Safari\//.test(agent) && !/Chrome\/|Chromium\/|Edg\//.test(agent)) return "safari";
  return "unsupported";
}

/** Show the browser's install prompt; whether the person installed the app. */
export async function install(): Promise<boolean> {
  if (!deferred) return false;
  const prompt = deferred;
  deferred = null;
  await prompt.prompt();
  const { outcome } = await prompt.userChoice;
  changed();
  return outcome === "accepted";
}

export function useInstallState(): InstallState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    installState,
    () => "unsupported",
  );
}
