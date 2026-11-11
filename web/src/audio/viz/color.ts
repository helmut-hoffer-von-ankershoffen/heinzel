// Theme colours for the visualizations, read from the live design tokens (light, dark and plugin themes). A token may
// hold any CSS colour (hex, rgb(), hsl(), oklch(), a name); the browser itself turns it into sRGB, so a theme in a
// wide-gamut notation colours the spectrum and aurora too. No colour is written here: every hue is a token.

export type Rgb = { readonly r: number; readonly g: number; readonly b: number };

/** Unresolvable everywhere (no tokens, no canvas): draws as transparent-black, never as a made-up brand hue. */
export const NO_COLOR: Rgb = { r: 0, g: 0, b: 0 };

let probe: CanvasRenderingContext2D | null | undefined;

function painter() {
  if (probe !== undefined) return probe;
  try {
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    probe = canvas.getContext("2d", { willReadFrequently: true });
  } catch {
    probe = null;
  }
  return probe;
}

function hex(text: string): Rgb | null {
  const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/.exec(text);
  if (!match?.[1]) return null;
  const digits = match[1].length === 3 ? [...match[1]].map((digit) => digit + digit).join("") : match[1];
  return {
    r: Number.parseInt(digits.slice(0, 2), 16),
    g: Number.parseInt(digits.slice(2, 4), 16),
    b: Number.parseInt(digits.slice(4, 6), 16),
  };
}

function rgbFunction(text: string): Rgb | null {
  const match = /^rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})/.exec(text);
  if (!match) return null;
  const channel = (part: string | undefined) => Math.max(0, Math.min(255, Number(part)));
  return { r: channel(match[1]), g: channel(match[2]), b: channel(match[3]) };
}

/** Any CSS colour as sRGB, or null when it is empty or not a colour. */
export function cssColor(value: string): Rgb | null {
  const text = value.trim().toLowerCase();
  if (!text) return null;
  const quick = hex(text) ?? rgbFunction(text);
  if (quick) return quick;
  if (typeof CSS === "undefined" || !CSS.supports?.("color", text)) return null;
  const context = painter();
  if (!context) return null;
  context.fillStyle = text;
  context.clearRect(0, 0, 1, 1);
  context.fillRect(0, 0, 1, 1);
  const [r = 0, g = 0, b = 0, alpha = 0] = context.getImageData(0, 0, 1, 1).data;
  return alpha ? { r, g, b } : null;
}

/** A token's colour, else the next token's, else the page's text colour: always the active theme's. */
export function tokenColor(style: CSSStyleDeclaration, ...names: string[]): Rgb {
  for (const name of names) {
    const color = cssColor(style.getPropertyValue(name));
    if (color) return color;
  }
  return cssColor(style.color) ?? NO_COLOR;
}

/**
 * Calls `onChange` whenever the theme may have changed: light/dark (the root's class), a plugin theme or its daypart
 * variant (the root's inline tokens), or the system colour scheme. Returns the unsubscribe.
 */
export function watchTheme(onChange: () => void) {
  const root = document.documentElement;
  const observer = typeof MutationObserver === "function" ? new MutationObserver(onChange) : null;
  observer?.observe(root, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
  const scheme = window.matchMedia?.("(prefers-color-scheme: dark)");
  scheme?.addEventListener?.("change", onChange);
  return () => {
    observer?.disconnect();
    scheme?.removeEventListener?.("change", onChange);
  };
}
