// What the splash in index.html needs before React loads: the last theme's colours, font and logo, and hz's version,
// kept in localStorage under `hz.splash`. index.html's inline script reads the same shape; keep the two in step.
import type { SchemaThemeView } from "@/api/client";

export const SNAPSHOT_KEY = "hz.splash";

type Colours = { background: string; foreground: string; muted_foreground: string; primary: string };
export type Snapshot = {
  light?: Colours;
  dark?: Colours;
  font?: string;
  logo?: { light: string; dark: string; alt: string; height: string };
  version?: string;
};

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null; // storage disabled: the splash shows the default theme
  }
}

/** The snapshot kept last time, or an empty one when there is none, it is damaged, or storage is off. */
export function readSnapshot(): Snapshot {
  try {
    const value: unknown = JSON.parse(storage()?.getItem(SNAPSHOT_KEY) ?? "null");
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Snapshot) : {};
  } catch {
    return {};
  }
}

/** Merge `change` into the snapshot; a key set to undefined is dropped. Never throws: it is a convenience. */
export function remember(change: Partial<Snapshot>): void {
  try {
    const next = { ...readSnapshot(), ...change };
    storage()?.setItem(SNAPSHOT_KEY, JSON.stringify(next));
  } catch {
    // Full or blocked: the next start shows the default theme.
  }
}

const colours = (mode: SchemaThemeView["light"]): Colours => ({
  background: mode.background,
  foreground: mode.foreground,
  muted_foreground: mode.muted_foreground,
  primary: mode.primary,
});

/** The part of the snapshot a theme decides; the built-in theme's is index.html's own CSS, so it keeps nothing. */
export function themeSnapshot(theme: SchemaThemeView | undefined): Partial<Snapshot> {
  if (!theme || theme.name === "default")
    return { light: undefined, dark: undefined, font: undefined, logo: undefined };
  const logo = theme.logo;
  return {
    light: colours(theme.light),
    dark: colours(theme.dark),
    font: theme.font_sans,
    logo: logo ? { light: logo.light, dark: logo.dark, alt: logo.alt, height: logo.height } : undefined,
  };
}
