// The terminal's colours come from the active theme's tokens, so it follows the app: background, foreground, the primary
// colour for the cursor and selection, ok, warn and bad for green, yellow and red, muted for black. A theme cannot set
// blue, magenta and cyan (it has no such tokens), so they are fixed per mode in design/tokens.json (`terminal-*`).
import type { ITheme } from "@xterm/xterm";

/** [light, dark] for the three ANSI hues a theme has no token for; `scripts/design_tokens.py --check` keeps them equal to design/tokens.json. */
const HUES = {
  blue: ["#2a5fb8", "#7aa7f0"],
  magenta: ["#8a3f9e", "#c79be0"],
  cyan: ["#17707a", "#6cc4c4"],
} as const;

type Rgb = [number, number, number];

/** The theme's tokens with the base theme's values, used when a token can't be read (no stylesheet, odd syntax). */
const FALLBACK = {
  light: {
    background: "#faf8f3",
    foreground: "#263b32",
    muted: "#eee8db",
    "muted-foreground": "#56675e",
    primary: "#a64028",
    ok: "#1f7444",
    warn: "#8a5c00",
    bad: "#b4372d",
  },
  dark: {
    background: "#18231e",
    foreground: "#e9ede5",
    muted: "#2b3b31",
    "muted-foreground": "#b4c3b5",
    primary: "#ffb398",
    ok: "#7cc39a",
    warn: "#e0b45f",
    bad: "#e5806f",
  },
} as const;
type Token = keyof (typeof FALLBACK)["light"];

function parse(value: string): Rgb | null {
  const text = value.trim().toLowerCase();
  const hex = /^#([0-9a-f]{3,8})$/.exec(text)?.[1];
  if (hex && [3, 4, 6, 8].includes(hex.length)) {
    const full = hex.length <= 4 ? [...hex].map((c) => c + c).join("") : hex;
    return [0, 2, 4].map((i) => Number.parseInt(full.slice(i, i + 2), 16)) as Rgb;
  }
  const rgb = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/.exec(text);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  const srgb = /^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/.exec(text);
  if (srgb) return [Number(srgb[1]) * 255, Number(srgb[2]) * 255, Number(srgb[3]) * 255];
  return null;
}

const hex = ([r, g, b]: Rgb) =>
  `#${[r, g, b]
    .map((c) =>
      Math.round(Math.min(255, Math.max(0, c)))
        .toString(16)
        .padStart(2, "0"),
    )
    .join("")}`;
/** `amount` of `b` mixed into `a`. */
const mix = (a: Rgb, b: Rgb, amount: number): Rgb => [
  a[0] * (1 - amount) + b[0] * amount,
  a[1] * (1 - amount) + b[1] * amount,
  a[2] * (1 - amount) + b[2] * amount,
];

/** The xterm theme for the active theme and mode, read from the tokens on <html> now. */
export function terminalTheme(root: HTMLElement = document.documentElement): ITheme {
  const dark = root.classList.contains("dark");
  const style = getComputedStyle(root);
  const fallback = FALLBACK[dark ? "dark" : "light"];
  const read = (name: Token): Rgb => parse(style.getPropertyValue(`--color-${name}`)) ?? (parse(fallback[name]) as Rgb);
  const background = read("background");
  const foreground = read("foreground");
  const primary = read("primary");
  const [red, green, yellow] = [read("bad"), read("ok"), read("warn")];
  const hue = (name: keyof typeof HUES) => parse(HUES[name][dark ? 1 : 0]) as Rgb;
  const [blue, magenta, cyan] = [hue("blue"), hue("magenta"), hue("cyan")];
  const bright = (color: Rgb) => hex(mix(color, foreground, 0.3));
  return {
    background: hex(background),
    foreground: hex(foreground),
    cursor: hex(primary),
    cursorAccent: hex(background),
    selectionBackground: `${hex(primary)}55`,
    black: hex(read("muted")),
    brightBlack: hex(read("muted-foreground")),
    red: hex(red),
    green: hex(green),
    yellow: hex(yellow),
    blue: hex(blue),
    magenta: hex(magenta),
    cyan: hex(cyan),
    white: hex(mix(foreground, background, 0.2)),
    brightRed: bright(red),
    brightGreen: bright(green),
    brightYellow: bright(yellow),
    brightBlue: bright(blue),
    brightMagenta: bright(magenta),
    brightCyan: bright(cyan),
    brightWhite: hex(foreground),
  };
}

/** Calls `apply` with the current theme now and on every change of mode or theme tokens (also after a variant's fade). */
export function followTerminalTheme(apply: (theme: ITheme) => void, root: HTMLElement = document.documentElement) {
  let later: ReturnType<typeof setTimeout> | undefined;
  const update = () => apply(terminalTheme(root));
  const observer = new MutationObserver(() => {
    update();
    // A variant fades its colours over 1.5 s (`.theme-shift`): read again once the fade is over.
    clearTimeout(later);
    later = setTimeout(update, 1600);
  });
  observer.observe(root, { attributes: true, attributeFilter: ["class", "style"] });
  return () => {
    observer.disconnect();
    clearTimeout(later);
  };
}
