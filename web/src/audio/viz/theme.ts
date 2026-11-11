import { type Rgb, tokenColor } from "./color";

export interface VizTheme {
  readonly a: Rgb;
  readonly b: Rgb;
  readonly c: Rgb;
  readonly dark: boolean;
}

/** The app's tokens: terracotta (primary), forest (ok) and lantern gold (warn); they follow light, dark and themes. */
export function readVizTheme(root: HTMLElement = document.documentElement): VizTheme {
  const style = getComputedStyle(root);
  return {
    a: tokenColor(style, "--color-primary"),
    b: tokenColor(style, "--color-ok", "--color-primary"),
    c: tokenColor(style, "--color-warn", "--color-primary"),
    dark: root.classList.contains("dark"),
  };
}

export function mixRgb(from: Rgb, to: Rgb, amount: number): Rgb {
  const at = (x: number, y: number) => Math.round(x + (y - x) * amount);
  return { r: at(from.r, to.r), g: at(from.g, to.g), b: at(from.b, to.b) };
}
