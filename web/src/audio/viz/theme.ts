import { parseColor, type Rgb } from "../aurora";

export interface VizTheme {
  readonly a: Rgb;
  readonly b: Rgb;
  readonly c: Rgb;
  readonly dark: boolean;
}

const DEFAULT: VizTheme = {
  a: { r: 166, g: 64, b: 40 },
  b: { r: 31, g: 116, b: 68 },
  c: { r: 183, g: 132, b: 28 },
  dark: false,
};

/** The app's tokens: terracotta (primary), forest (ok) and lantern gold (warn); they follow light, dark and themes. */
export function readVizTheme(root: HTMLElement = document.documentElement): VizTheme {
  const style = getComputedStyle(root);
  return {
    a: parseColor(style.getPropertyValue("--color-primary"), DEFAULT.a),
    b: parseColor(style.getPropertyValue("--color-ok"), DEFAULT.b),
    c: parseColor(style.getPropertyValue("--color-warn"), DEFAULT.c),
    dark: root.classList.contains("dark"),
  };
}

export function mixRgb(from: Rgb, to: Rgb, amount: number): Rgb {
  const at = (x: number, y: number) => Math.round(x + (y - x) * amount);
  return { r: at(from.r, to.r), g: at(from.g, to.g), b: at(from.b, to.b) };
}
