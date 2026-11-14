// A window that runs an older web build than the one hz serves now (heinzel-management-uke3): an update installed and
// restarted hz from the CLI, the desktop app, another window or on its own, while this page kept its old code. The
// page notices by asking for the served page (`GET /`, never a cached copy) and comparing its hashed entrypoints with
// its own, and reloads itself when that is safe; AutoUpdate.tsx asks and shows the notices.
//
// Safe means: no chat turn or message on its way, no dialog, alert or terminal open, no unsent input that nothing
// saves (any field typed into, or a registered editor), and no unsent chat message while the window is in front. A
// window in the background saves its chat composer drafts first (they restore after the reload), so nobody waits for
// a window they don't look at; every other unsent input holds the reload there too. A reload that did not bring the
// served build is not repeated for that build, and a window reloads by itself at most twice in ten minutes (a server
// flapping between builds must not loop it): after that it says to reload.
import { entrypointsOf, pageEntrypoints } from "./webBuild";
import { reloadHeld, saveOpenWork, unsavedInput, workOnScreen, workRunning } from "./work";

const TRIED = "hz.update.staleBuild";
const NOTICE = "hz.update.reloaded";
const RELOADS = "hz.update.autoReloads";
/** At most this many automatic reloads per tab within `RELOAD_WINDOW_MS`. */
export const MAX_AUTO_RELOADS = 2;
export const RELOAD_WINDOW_MS = 10 * 60_000;
const MAX_PAGE_BYTES = 1024 * 1024;

/** The entrypoints of the page hz serves now; null when it can't be asked or serves no hashed build (a dev server). */
export async function servedEntrypoints(signal?: AbortSignal): Promise<string[] | null> {
  try {
    const response = await fetch(new Request(new URL("/", location.origin), { cache: "no-store", signal }));
    if (!response.ok || !(response.headers.get("Content-Type") ?? "").startsWith("text/html")) return null;
    const html = await response.text();
    if (html.length > MAX_PAGE_BYTES) return null;
    const paths = entrypointsOf(new DOMParser().parseFromString(html, "text/html"));
    return paths.length > 0 ? paths : null;
  } catch {
    return null; // hz is restarting or away: ask again later
  }
}

const key = (paths: readonly string[]) => paths.join("\n");

/** Whether hz serves another build than the one this page runs; never for a page without a build of its own. */
export function isStale(served: readonly string[] | null, page: readonly string[] = pageEntrypoints()): boolean {
  return page.length > 0 && served !== null && served.length > 0 && key(served) !== key(page);
}

/** Whether this window already reloaded for this served build and still runs another: reloading again won't help. */
export function reloadedFor(served: readonly string[]): boolean {
  try {
    return sessionStorage.getItem(TRIED) === key(served);
  } catch {
    return false;
  }
}

export type Blocker = "turn" | "screen" | "input" | "draft" | null;

/**
 * What holds the reload now: a turn or message on its way, open work on screen, unsent input nothing saves (in front
 * and in the background), or an unsent chat message in front (in the background it is saved and restored).
 */
export function reloadBlocker(visible = document.visibilityState === "visible"): Blocker {
  if (workRunning()) return "turn";
  if (workOnScreen()) return "screen";
  if (unsavedInput()) return "input";
  return visible && reloadHeld() ? "draft" : null;
}

function reloadTimes(now: number): number[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(RELOADS) ?? "[]");
    return Array.isArray(value)
      ? value.filter((at): at is number => typeof at === "number" && now - at < RELOAD_WINDOW_MS)
      : [];
  } catch {
    return [];
  }
}

/** Whether this tab may still reload by itself: fewer than `MAX_AUTO_RELOADS` within the last ten minutes. */
export function autoReloadAllowed(now = Date.now()): boolean {
  return reloadTimes(now).length < MAX_AUTO_RELOADS;
}

/** Count one automatic reload of this tab. */
export function countAutoReload(now = Date.now()): void {
  try {
    sessionStorage.setItem(RELOADS, JSON.stringify([...reloadTimes(now), now]));
  } catch {
    // no storage: the per-build guard still stops a loop on one build
  }
}

/**
 * Save unsent work and remember why this window reloads, so the reloaded page restores the text and says what
 * happened; false when the work can't be saved (then the window keeps its page).
 */
export function prepareReload(served: readonly string[] | null): boolean {
  try {
    saveOpenWork();
    if (served) sessionStorage.setItem(TRIED, key(served));
    sessionStorage.setItem(NOTICE, "1");
  } catch {
    return false;
  }
  return true;
}

/** Whether this page came up from an update reload; true once per reload, so the page says so once. */
export function takeReloadNotice(): boolean {
  try {
    const left = sessionStorage.getItem(NOTICE) !== null;
    sessionStorage.removeItem(NOTICE);
    return left;
  } catch {
    return false;
  }
}

/** The page now runs the build hz serves: a later update may reload it again. */
export function forgetReload(): void {
  try {
    sessionStorage.removeItem(TRIED);
  } catch {
    // nothing remembered
  }
}
