// Which visualizations show, per person: one master switch and one switch per style, all on by default, kept in this
// browser like the app's other interface preferences and applied at once everywhere they are read.
import { useSyncExternalStore } from "react";

export const STYLES = ["spectrum", "aurora"] as const;
export type VizStyle = (typeof STYLES)[number];
export type VizSettings = { readonly master: boolean; readonly styles: Readonly<Record<VizStyle, boolean>> };

const KEY = "hz.audio.viz";
export const DEFAULT_SETTINGS: VizSettings = { master: true, styles: { spectrum: true, aurora: true } };

export function parseSettings(raw: string | null): VizSettings {
  if (!raw) return DEFAULT_SETTINGS;
  try {
    const data = JSON.parse(raw) as { master?: unknown; styles?: Record<string, unknown> };
    const styles = { ...DEFAULT_SETTINGS.styles };
    for (const style of STYLES) if (typeof data.styles?.[style] === "boolean") styles[style] = data.styles[style];
    return { master: typeof data.master === "boolean" ? data.master : true, styles };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function load(): VizSettings {
  try {
    return parseSettings(localStorage.getItem(KEY));
  } catch {
    return DEFAULT_SETTINGS;
  }
}

let current = load();
const listeners = new Set<() => void>();

function commit(next: VizSettings) {
  current = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // not remembered; still applied now
  }
  for (const listener of listeners) listener();
}

export const setMaster = (master: boolean) => commit({ ...current, master });
export const setStyle = (style: VizStyle, on: boolean) =>
  commit({ ...current, styles: { ...current.styles, [style]: on } });
/** For tests: reread the stored value. */
export const reloadSettings = () => {
  current = load();
  for (const listener of listeners) listener();
};

export function useVizSettings(): VizSettings {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}

/** Whether a style draws: the master switch and its own switch. */
export const styleOn = (settings: VizSettings, style: VizStyle) => settings.master && settings.styles[style];
