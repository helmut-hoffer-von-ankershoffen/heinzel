/** The right sidebar's width: one value for Chat, Terminal, Calendar and the other panels, remembered per browser profile. */
export const SIDEBAR_DEFAULT = 384;
export const SIDEBAR_MIN = 320;
/** The least the page content keeps beside the sidebar. */
export const CONTENT_MIN = 360;
export const SIDEBAR_STEP = 16;
export const SIDEBAR_STEP_LARGE = 64;
const KEY = "hz.workspace.width";

/** The widest the sidebar may be: 70% of the window and never leaving the content `CONTENT_MIN`; never below the minimum. */
export function sidebarMax(windowPx: number, areaPx: number = windowPx): number {
  return Math.max(SIDEBAR_MIN, Math.floor(Math.min(windowPx * 0.7, areaPx - CONTENT_MIN)));
}

export function clampSidebar(width: number, windowPx: number, areaPx: number = windowPx): number {
  if (!Number.isFinite(width)) return Math.min(SIDEBAR_DEFAULT, sidebarMax(windowPx, areaPx));
  return Math.round(Math.min(Math.max(width, SIDEBAR_MIN), sidebarMax(windowPx, areaPx)));
}

export function readSidebarWidth(): number {
  try {
    const saved = Number.parseFloat(localStorage.getItem(KEY) ?? "");
    if (Number.isFinite(saved) && saved > 0) return saved;
  } catch {
    // No storage: the default.
  }
  return SIDEBAR_DEFAULT;
}

export function writeSidebarWidth(width: number): void {
  try {
    localStorage.setItem(KEY, String(Math.round(width)));
  } catch {
    // Not remembered.
  }
}
