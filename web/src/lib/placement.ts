import { viewportBounds } from "./viewport";

/** Keep a card of `width` by `height` inside a window of `viewport`. */
export function clampToWindow(
  x: number,
  y: number,
  size: { width: number; height: number },
  viewport: { width: number; height: number; left?: number; top?: number } = viewportBounds(),
): { x: number; y: number } {
  return {
    x: Math.min(
      Math.max(viewport.left ?? 0, x),
      Math.max(viewport.left ?? 0, (viewport.left ?? 0) + viewport.width - size.width),
    ),
    y: Math.min(
      Math.max(viewport.top ?? 0, y),
      Math.max(viewport.top ?? 0, (viewport.top ?? 0) + viewport.height - size.height),
    ),
  };
}

const MARGIN = 8;
type Box = { left: number; top: number; right: number; bottom: number };

/** Limit an above/below pane to the larger free side, so it never covers its own trigger. */
export function popoverHeight(anchor: Box): number {
  const viewport = viewportBounds();
  return Math.max(
    1,
    Math.min(
      viewport.height - 2 * MARGIN,
      Math.max(anchor.top - viewport.top - MARGIN - 6, viewport.top + viewport.height - anchor.bottom - MARGIN - 6),
    ),
  );
}

/**
 * Where a popover of `size` goes next to `anchor` and stays inside the window: above it, right-aligned (the footer),
 * or beside it (the collapsed rail); on the other side when there is no room, and shifted in as the last resort.
 */
export function placePopover(
  anchor: Box,
  size: { width: number; height: number },
  side: "above" | "below" | "right",
  viewport: { width: number; height: number; left?: number; top?: number } = viewportBounds(),
): { left: number; top: number } {
  const startX = viewport.left ?? 0;
  const startY = viewport.top ?? 0;
  let left: number;
  let top: number;
  if (side === "above") {
    left = anchor.right - size.width;
    top = anchor.top - size.height - 6;
    if (top < startY + MARGIN) top = anchor.bottom + 6;
  } else if (side === "below") {
    left = anchor.left;
    top = anchor.bottom + 6;
    if (top + size.height > startY + viewport.height - MARGIN) top = anchor.top - size.height - 6;
  } else {
    left = anchor.right + MARGIN;
    top = anchor.bottom - size.height;
    if (left + size.width > startX + viewport.width - MARGIN) left = anchor.left - size.width - MARGIN;
  }
  return {
    left: Math.max(startX + MARGIN, Math.min(left, startX + viewport.width - size.width - MARGIN)),
    top: Math.max(startY + MARGIN, Math.min(top, startY + viewport.height - size.height - MARGIN)),
  };
}
