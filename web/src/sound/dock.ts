// Where the stream's mini-player lives: docked in the left sidebar's column, or floating over the content. Whether it
// is docked and where it floats are remembered per viewer in this browser (storage may be unavailable, which is fine).
// The sidebar offers its dock slot while it is expanded; without a slot (the rail) the player floats, and it docks
// again when the slot returns, because the preference to be docked is kept.
import { useSyncExternalStore } from "react";

const KEY = "hz.sound.player";

export type DockPref = { docked: boolean; x: number | null; y: number | null };
const DEFAULT: DockPref = { docked: true, x: null, y: null };

function load(): DockPref {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<DockPref> | null;
    if (!raw) return DEFAULT;
    const coordinate = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);
    return { docked: raw.docked !== false, x: coordinate(raw.x), y: coordinate(raw.y) };
  } catch {
    return DEFAULT;
  }
}

let pref = load();
let slot: HTMLElement | null = null;
const listeners = new Set<() => void>();
const emit = () => {
  for (const listener of listeners) listener();
};
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

function save(next: DockPref): void {
  pref = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Not remembered; it still holds for this tab.
  }
  emit();
}

/** The remembered choice: docked or floating, and where it floats. */
export const useDockPref = (): DockPref => useSyncExternalStore(subscribe, () => pref);
export const setDocked = (docked: boolean): void => save({ ...pref, docked });
/** Float at `x`, `y` (the card's left and top, in the window's pixels). */
export const setFloating = (x: number, y: number): void => save({ docked: false, x, y });

/** The sidebar's slot, handed over by a ref; null while the sidebar is collapsed. */
export function registerSlot(element: HTMLElement | null): void {
  if (slot === element) return;
  slot = element;
  emit();
}
export const useDockSlot = (): HTMLElement | null => useSyncExternalStore(subscribe, () => slot);

/** For tests: forget the remembered choice and the slot. */
export function resetDock(): void {
  pref = DEFAULT;
  slot = null;
  try {
    localStorage.removeItem(KEY);
  } catch {
    // nothing stored
  }
  emit();
}

/** Keep a card of `width` by `height` inside a window of `viewport`. */
export function clampToWindow(
  x: number,
  y: number,
  size: { width: number; height: number },
  viewport = { width: window.innerWidth, height: window.innerHeight },
): { x: number; y: number } {
  return {
    x: Math.min(Math.max(0, x), Math.max(0, viewport.width - size.width)),
    y: Math.min(Math.max(0, y), Math.max(0, viewport.height - size.height)),
  };
}

/** Within this many pixels of the dock spot, letting go docks the player. */
export const SNAP_PX = 80;
export const nearSlot = (x: number, y: number, rect: { left: number; top: number }): boolean =>
  Math.abs(x - rect.left) <= SNAP_PX && Math.abs(y - rect.top) <= SNAP_PX;

const MARGIN = 8;
type Box = { left: number; top: number; right: number; bottom: number };

/**
 * Where a popover of `size` goes next to `anchor` and stays inside the window: above it, right-aligned (the footer),
 * or beside it (the collapsed rail); on the other side when there is no room, and shifted in as the last resort.
 */
export function placePopover(
  anchor: Box,
  size: { width: number; height: number },
  side: "above" | "right",
  viewport = { width: window.innerWidth, height: window.innerHeight },
): { left: number; top: number } {
  let left: number;
  let top: number;
  if (side === "above") {
    left = anchor.right - size.width;
    top = anchor.top - size.height - 6;
    if (top < MARGIN) top = anchor.bottom + 6;
  } else {
    left = anchor.right + MARGIN;
    top = anchor.bottom - size.height;
    if (left + size.width > viewport.width - MARGIN) left = anchor.left - size.width - MARGIN;
  }
  return {
    left: Math.max(MARGIN, Math.min(left, viewport.width - size.width - MARGIN)),
    top: Math.max(MARGIN, Math.min(top, viewport.height - size.height - MARGIN)),
  };
}
