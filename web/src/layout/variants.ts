// A theme's variants (ADR 0060): colours that follow the signals, the time of day and the weather. The variant naming
// most signals wins, the first on a tie, exactly as `variant_of` in the kernel. A backdrop is built from its typed
// parts only: a hex stop or a known token, an angle and an opacity checked again here; anything else draws nothing.
import type {
  SchemaThemeBackdrop,
  SchemaThemeColours,
  SchemaThemeSignalsView,
  SchemaThemeVariant,
  SchemaThemeView,
} from "@/api/client";

export type Signals = Partial<Pick<SchemaThemeSignalsView, "daypart" | "weather">>;

export const DAYPARTS = ["dawn", "morning", "day", "dusk", "evening", "night"] as const;
export const WEATHERS = [
  "clear",
  "clouds",
  "rain",
  "drizzle",
  "thunder",
  "snow",
  "fog",
  "wind",
  "heat",
  "cold",
] as const;

const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;
const TOKENS = new Set([
  "background",
  "foreground",
  "muted",
  "muted_foreground",
  "card",
  "border",
  "primary",
  "primary_foreground",
  "accent",
  "ok",
  "warn",
  "bad",
  "ring",
  "sidebar",
  "input",
  "popover",
  "accent_foreground",
  "icon",
  "main_tint",
]);
const MAX_OPACITY = 0.9;

function matches(variant: SchemaThemeVariant, signals: Signals): boolean {
  const { daypart = [], weather = [] } = variant.when;
  return (
    (daypart.length === 0 || (signals.daypart != null && daypart.includes(signals.daypart))) &&
    (weather.length === 0 || (signals.weather != null && weather.includes(signals.weather)))
  );
}

const specificity = (variant: SchemaThemeVariant) =>
  Number((variant.when.daypart ?? []).length > 0) + Number((variant.when.weather ?? []).length > 0);

/** The index of the variant in effect, or -1 when none matches. */
export function variantOf(theme: SchemaThemeView, signals: Signals): number {
  let best = -1;
  (theme.variant ?? []).forEach((variant, index) => {
    if (matches(variant, signals) && (best < 0 || specificity(variant) > specificity(theme.variant?.[best] ?? variant)))
      best = index;
  });
  return best;
}

/** The colours of the mode shown, with the variant the signals pick applied over the theme's own. */
export function coloursOf(theme: SchemaThemeView, dark: boolean, signals: Signals): SchemaThemeColours {
  const base = dark ? theme.dark : theme.light;
  const variant = theme.variant?.[variantOf(theme, signals)];
  const overrides = variant ? (dark ? variant.dark : variant.light) : undefined;
  if (!overrides) return base;
  const set = Object.fromEntries(Object.entries(overrides).filter(([, value]) => value != null));
  return { ...base, ...set } as SchemaThemeColours;
}

/** The CSS of a backdrop, built from its checked parts, or `none`. */
export function backdropCss(backdrop: SchemaThemeBackdrop | null | undefined): string {
  if (!backdrop) return "none";
  const { gradient, angle = 180, stops, opacity = 0.6 } = backdrop;
  const valid =
    (gradient === "linear" || gradient === "radial") &&
    Number.isInteger(angle) &&
    angle >= 0 &&
    angle <= 360 &&
    typeof opacity === "number" &&
    opacity >= 0 &&
    opacity <= MAX_OPACITY &&
    stops.length >= 2 &&
    stops.length <= 4 &&
    stops.every((stop) => HEX.test(stop) || TOKENS.has(stop));
  if (!valid) return "none";
  const percent = Math.round(opacity * 100);
  const colours = stops.map((stop) => {
    const colour = HEX.test(stop) ? stop : `var(--color-${stop.replaceAll("_", "-")})`;
    return `color-mix(in srgb, ${colour} ${percent}%, transparent)`;
  });
  return gradient === "linear"
    ? `linear-gradient(${angle}deg, ${colours.join(", ")})`
    : `radial-gradient(circle at 50% 0%, ${colours.join(", ")})`;
}
