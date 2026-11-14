/**
 * Lost canvases: a GPU reset takes every 2D context on the page with it. The browser usually restores them within half
 * a second; when it does not, the canvases stay blank (Chromium paints its broken-canvas icon) while the loop keeps
 * drawing into nothing. Then only new canvas elements, with new contexts, show the visualization again.
 */

/** How long a lost context may stay lost before the canvases are replaced; Chromium restores within about 500 ms. */
export const LOST_GRACE_MS = 1000;
/** Replacements after which the canvases draw in memory (`willReadFrequently`), off the GPU that keeps failing. */
export const SOFTWARE_AFTER = 2;

type Lossy = { isContextLost?: () => boolean };

function anyLost(contexts: readonly (Lossy | null)[]) {
  return contexts.some((context) => context?.isContextLost?.() === true);
}

export interface LostWatch {
  /** Call once per drawn frame: starts the grace time for a loss the browser did not announce with an event. */
  readonly check: () => void;
  readonly stop: () => void;
}

/**
 * Calls `restored` when the browser brings a lost context back, and `gone` once if a context is still lost
 * `LOST_GRACE_MS` after it was lost.
 */
export function watchLost(
  canvases: readonly HTMLCanvasElement[],
  contexts: readonly (Lossy | null)[],
  restored: () => void,
  gone: () => void,
): LostWatch {
  let timer = 0;
  let done = false;
  const expire = () => {
    timer = 0;
    if (done || !anyLost(contexts)) return;
    done = true;
    gone();
  };
  const arm = () => {
    if (!timer && !done) timer = window.setTimeout(expire, LOST_GRACE_MS);
  };
  const back = () => {
    if (done) return;
    if (!anyLost(contexts)) {
      window.clearTimeout(timer);
      timer = 0;
    }
    restored();
  };
  for (const canvas of canvases) {
    canvas.addEventListener("contextlost", arm);
    canvas.addEventListener("contextrestored", back);
  }
  return {
    check: () => {
      if (!timer && anyLost(contexts)) arm();
    },
    stop: () => {
      done = true;
      window.clearTimeout(timer);
      for (const canvas of canvases) {
        canvas.removeEventListener("contextlost", arm);
        canvas.removeEventListener("contextrestored", back);
      }
    },
  };
}
