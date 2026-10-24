// Applies the selected theme (ADR 0044) as CSS custom properties on <html>, for light or dark as the page shows.
// The default theme sets nothing: index.css is its source, so the app looks right before the list arrives.
import { useEffect, useState } from "react";
import type { SchemaThemeView } from "@/api/client";
import { useThemes } from "@/api/queries";

const DEFAULT = "default";

function useDark(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(document.documentElement.classList.contains("dark")));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

/** `muted_foreground` → `--color-muted-foreground`, plus the fonts. */
export function tokens(theme: SchemaThemeView, dark: boolean): Record<string, string> {
  const colours = Object.entries(dark ? theme.dark : theme.light).map(([key, value]) => [
    `--color-${key.replaceAll("_", "-")}`,
    String(value),
  ]);
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

export function ThemeTokens() {
  const themes = useThemes();
  const dark = useDark();
  const selected = themes.data?.themes.find((theme) => theme.name === themes.data?.selected);
  useEffect(() => {
    const root = document.documentElement.style;
    if (!selected || selected.name === DEFAULT) return;
    const applied = tokens(selected, dark);
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
  }, [selected, dark]);
  return null;
}
