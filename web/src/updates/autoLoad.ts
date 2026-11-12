// Loading a core that is installed but not running, on its own (heinzel-management-uke3). It waits for the same
// server-side idle check automatic updates wait for and for open work in this window, and only one window acts. Each
// version is tried on its own once across this app's windows: a persistent failure never restarts every window in
// turn. The "Load Heinzel" button stays the explicit way to try again.

import type { UpdateStatus } from "./controller";
import { reloadHeld, workOnScreen } from "./work";

const TRIED = "hz.update.autoLoaded";
const triedHere = new Set<string>(); // when this browser keeps no local storage

export function autoLoadTried(version: string): boolean {
  try {
    return localStorage.getItem(TRIED) === version || triedHere.has(version);
  } catch {
    return triedHere.has(version);
  }
}

function markTried(version: string): void {
  try {
    localStorage.setItem(TRIED, version);
    if (localStorage.getItem(TRIED) === version) return;
  } catch {
    // remembered in this window only
  }
  triedHere.add(version);
}

export type AutoLoadOutcome = "loaded" | "tried" | "gone" | "held" | "elsewhere";

type Locks = Pick<LockManager, "request">;

/** One attempt; "held" and "elsewhere" mean ask again shortly, the others end automatic loading of this version. */
export async function autoLoadInstalledCore(
  version: string,
  load: (version: string) => Promise<unknown>,
  readStatus: () => Promise<UpdateStatus>,
  locks: Locks | undefined = typeof navigator === "undefined" ? undefined : navigator.locks,
): Promise<AutoLoadOutcome> {
  if (autoLoadTried(version)) return "tried";
  if (reloadHeld() || workOnScreen()) return "held";
  const attempt = async (): Promise<AutoLoadOutcome> => {
    if (autoLoadTried(version)) return "tried"; // another window took it while this one waited
    const fresh = await readStatus();
    if (fresh.installed !== version) return "gone";
    if (fresh.idle === false || reloadHeld() || workOnScreen()) return "held";
    markTried(version);
    await load(version);
    return "loaded";
  };
  if (!locks) return attempt();
  return locks.request("hz-load-installed-core", { ifAvailable: true }, (lock) =>
    lock ? attempt() : Promise.resolve<AutoLoadOutcome>("elsewhere"),
  );
}
