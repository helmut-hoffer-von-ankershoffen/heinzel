// A window that runs an older web build than the one hz serves now (heinzel-management-uke3): an update installed and
// restarted hz from the CLI, the desktop app, another window or on its own, while this page kept its old code. The
// page notices by asking for the served page (`GET /`, never a cached copy) and comparing its hashed entrypoints with
// its own, and reloads itself when that is safe; AutoUpdate.tsx asks and shows the notices.
//
// Safe means: no chat turn or message on its way, no dialog, alert or terminal open, and no unsent text while the
// window is in front. A window in the background with unsent text saves it first (the composers restore it after the
// reload), so nobody's half-written message is lost and nobody waits for a window they don't look at. A reload that
// did not bring the served build is not repeated for the same build: the window says to reload instead.
import { entrypointsOf, pageEntrypoints } from "./webBuild";
import { reloadHeld, saveOpenWork, workOnScreen, workRunning } from "./work";

const TRIED = "hz.update.staleBuild";
const NOTICE = "hz.update.reloaded";
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

export type Blocker = "turn" | "screen" | "draft" | null;

/** What holds the reload now: a turn or message on its way, open work on screen, or unsent text in front. */
export function reloadBlocker(visible = document.visibilityState === "visible"): Blocker {
  if (workRunning()) return "turn";
  if (workOnScreen()) return "screen";
  return visible && reloadHeld() ? "draft" : null;
}

/**
 * Save unsent work and remember why this window reloads, so the reloaded page restores the text and says what
 * happened; false when the work can't be saved (then the window keeps its page).
 */
export function prepareReload(served: readonly string[] | null): boolean {
  const draft = reloadHeld();
  try {
    saveOpenWork();
    if (served) sessionStorage.setItem(TRIED, key(served));
    sessionStorage.setItem(NOTICE, JSON.stringify({ draft }));
  } catch {
    return false;
  }
  return true;
}

export type ReloadNotice = { draft: boolean };

/** The notice the reload left for the page that came up, once; null after a reload nobody asked for. */
export function takeReloadNotice(): ReloadNotice | null {
  try {
    const raw = sessionStorage.getItem(NOTICE);
    sessionStorage.removeItem(NOTICE);
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw);
    return { draft: Boolean(value && typeof value === "object" && (value as ReloadNotice).draft === true) };
  } catch {
    return null;
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
