// Applies the selected theme (ADR 0044) as CSS custom properties on <html>, for light or dark as the page shows.
// The default theme sets nothing: index.css is its source, so the app looks right before the list arrives.
// Its variant (ADR 0058) follows the signals; a change of variant fades over 1.5 s (`.theme-shift` in index.css), never
// on the first paint or a switch of mode, and not at all with reduced motion.
import { useEffect, useRef, useState } from "react";
import type { SchemaThemeView } from "@/api/client";
import { useThemeSignals, useThemes } from "@/api/queries";
import { backdropCss, coloursOf, type Signals, variantOf } from "./variants";

const DEFAULT = "default";

/** Whether the page shows dark mode now (`.dark` on <html>), following every switch. */
export function useDark(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(document.documentElement.classList.contains("dark")));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

/** `muted_foreground` → `--color-muted-foreground`, plus the fonts. */
export function tokens(theme: SchemaThemeView, dark: boolean, signals: Signals = {}): Record<string, string> {
  // An optional colour the theme leaves out (`main_tint`) keeps the app's default; the backdrop is no colour.
  const effective = coloursOf(theme, dark, signals);
  const colours = Object.entries(effective)
    .filter(([key, value]) => key !== "backdrop" && typeof value === "string")
    .map(([key, value]) => [`--color-${key.replaceAll("_", "-")}`, String(value)]);
  colours.push(["--theme-backdrop", backdropCss(effective.backdrop)]);
  // Type scale, density and corners (ADR 0044); a field the server doesn't send keeps the app's default.
  const extra = theme as unknown as Record<string, unknown>;
  const scale: [string, string][] = [
    ["font_size", "--text-ui"],
    ["font_size_small", "--text-ui-sm"],
    ["icon_size", "--icon"],
    ["row_height", "--row"],
    ["radius_control", "--radius-lg"],
    ["radius", "--radius-xl"],
    ["heading_weight", "--font-weight-medium"],
    ["heading_tracking", "--heading-tracking"],
    ["spacing", "--spacing"],
  ];
  const sized = scale.flatMap(([field, name]) =>
    typeof extra[field] === "string" || typeof extra[field] === "number" ? [[name, String(extra[field])]] : [],
  );
  return Object.fromEntries([...colours, ...sized, ["--font-sans", theme.font_sans], ["--font-mono", theme.font_mono]]);
}

/** The theme in effect, once the list arrived. */
export function useSelectedTheme(): SchemaThemeView | undefined {
  const themes = useThemes();
  return themes.data?.themes.find((theme) => theme.name === themes.data?.selected);
}

/** The signals now, as the variants read them. */
export function useSignals(): Signals {
  const signals = useThemeSignals();
  return { daypart: signals.data?.daypart, weather: signals.data?.weather };
}

const SHIFT = "theme-shift";
const SHIFT_MS = 2000;

export function ThemeTokens() {
  const themes = useThemes();
  const dark = useDark();
  const signals = useSignals();
  const selected = themes.data?.themes.find((theme) => theme.name === themes.data?.selected);
  const variant = selected ? `${selected.name}:${variantOf(selected, signals)}` : "";
  const shown = useRef(variant);
  // A new variant of the same theme fades in; a new theme or mode switches at once.
  useEffect(() => {
    const before = shown.current;
    shown.current = variant;
    if (!before || before === variant || before.split(":")[0] !== variant.split(":")[0]) return;
    const html = document.documentElement;
    html.classList.add(SHIFT);
    const timer = window.setTimeout(() => html.classList.remove(SHIFT), SHIFT_MS);
    return () => window.clearTimeout(timer);
  }, [variant]);
  const { daypart, weather } = signals;
  useEffect(() => {
    const root = document.documentElement.style;
    if (!selected || selected.name === DEFAULT) return;
    const applied = tokens(selected, dark, { daypart, weather });
    for (const [name, value] of Object.entries(applied)) root.setProperty(name, value);
    // Fonts the theme ships, served by hz from the plugin (same origin, as the CSP wants).
    const fonts = (
      (selected as unknown as { fonts?: { family: string; url: string; weight?: string; style?: string }[] }).fonts ??
      []
    ).map(
      (font) =>
        new FontFace(font.family, `url(${font.url})`, { weight: font.weight ?? "400", style: font.style ?? "normal" }),
    );
    for (const font of fonts) {
      document.fonts.add(font);
      void font.load().catch(() => undefined);
    }
    return () => {
      for (const name of Object.keys(applied)) root.removeProperty(name);
      for (const font of fonts) document.fonts.delete(font);
    };
  }, [selected, dark, daypart, weather]);
  return null;
}
