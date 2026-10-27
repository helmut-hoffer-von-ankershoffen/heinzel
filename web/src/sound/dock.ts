// Where the stream's mini-player lives: docked in the left sidebar's column, or floating over the content. Whether it
// is docked and where it floats are remembered per viewer in this browser (storage may be unavailable, which is fine).
// Both sidebar widths offer a dock slot. The collapsed rail keeps the stream mounted while its compact controls
// replace the full card; expanding restores the card without restarting playback.
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

/** The sidebar's slot, handed over by a ref; the rail marks its slot as compact. */
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

/** Within this many pixels of the dock spot, letting go docks the player. */
export const SNAP_PX = 80;
export const nearSlot = (x: number, y: number, rect: { left: number; top: number }): boolean =>
  Math.abs(x - rect.left) <= SNAP_PX && Math.abs(y - rect.top) <= SNAP_PX;
