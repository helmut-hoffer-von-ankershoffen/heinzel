// `hz system theme use` as big previews: one card per theme, each a miniature of the app (a sidebar with two
// navigation rows, a header, a card, a button and an input) drawn with that theme's own tokens in the mode shown now.
// The tokens are set as custom properties on the preview's root, from the list's typed values (ADR 0044); nothing is
// built into CSS text, and only the selected theme's font files are loaded (the others show their stack's fallback).
import { Home, Search } from "lucide-react";
// Each preview shows the variant the signals pick now (ADR 0060); in expert mode, "Preview conditions" picks another
// daypart and weather for the previews only, to see every variant.
import { type CSSProperties, useId, useState } from "react";
import type { SchemaThemeView } from "@/api/client";
import { useExpert, useSetTheme, useThemeSignals, useThemes } from "@/api/queries";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { Branding } from "./Branding";
import { tokens, useDark } from "./ThemeTokens";
import { DAYPARTS, type Signals, WEATHERS } from "./variants";

/** The corners index.css derives from the two theme radii, declared again on the preview so they use its radii. */
const DERIVED: Record<string, string> = {
  "--radius-xs": "calc(var(--radius-lg) * 0.25)",
  "--radius": "calc(var(--radius-lg) * 0.5)",
  "--radius-sm": "calc(var(--radius-lg) * 0.5)",
  "--radius-md": "calc(var(--radius-lg) * 0.75)",
  "--radius-2xl": "calc(var(--radius-xl) * 4 / 3)",
  "--radius-3xl": "calc(var(--radius-xl) * 2)",
};

/** The style of a preview's root: the theme's tokens for `dark` or light, its font stack, and the derived corners. */
export function previewStyle(theme: SchemaThemeView, dark: boolean, signals: Signals = {}): CSSProperties {
  // A theme without a tint shows none, whatever the theme in use has.
  return {
    "--color-main-tint": "transparent",
    ...tokens(theme, dark, signals),
    ...DERIVED,
    fontFamily: theme.font_sans,
  } as CSSProperties;
}

export function ThemePreviews() {
  const themes = useThemes();
  const change = useSetTheme();
  const dark = useDark();
  const list = themes.data?.themes ?? [];
  const selected = themes.data?.selected ?? "default";
  const note = change.error?.message ?? themes.data?.note;
  const labelId = useId();
  const current = useThemeSignals();
  const now: Signals = { daypart: current.data?.daypart, weather: current.data?.weather };
  const expert = useExpert().data?.expert === true;
  const [conditions, setConditions] = useState<Signals | null>(null);
  const signals = conditions ?? now;
  return (
    <div className="border-b border-border/70 py-3">
      <p id={labelId} className="text-ui">
        {t("settings.theme")}
      </p>
      {note && <p className="mt-0.5 text-sm text-muted-foreground">{note}</p>}
      {expert && (
        <Conditions
          signals={signals}
          currentWeather={now.weather}
          loading={current.isPending}
          failed={current.isError}
          customized={conditions !== null}
          onChange={setConditions}
        />
      )}
      <div
        role="radiogroup"
        aria-labelledby={labelId}
        className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-3"
      >
        {list.map((theme) => (
          <ThemeCard
            key={theme.name}
            theme={theme}
            dark={dark}
            signals={signals}
            selected={theme.name === selected}
            disabled={change.isPending}
            onSelect={() => theme.name !== selected && change.mutate(theme.name)}
          />
        ))}
      </div>
    </div>
  );
}

/** Expert mode: the daypart and weather the previews show, instead of the signals now. */
function Conditions({
  signals,
  currentWeather,
  loading,
  failed,
  customized,
  onChange,
}: {
  signals: Signals;
  currentWeather: Signals["weather"];
  loading: boolean;
  failed: boolean;
  customized: boolean;
  onChange: (signals: Signals | null) => void;
}) {
  const hint = useId();
  const weatherStatus = loading
    ? t("settings.previewLoading")
    : failed
      ? t("settings.previewRefreshFailed")
      : currentWeather == null
        ? t("settings.previewWeatherUnavailable")
        : t("settings.previewCurrentWeather", { weather: t(`settings.preview.weather.${currentWeather}`) });
  return (
    <fieldset
      aria-describedby={hint}
      className="mt-2 min-w-0 rounded-lg border border-border p-3 text-ui-sm text-muted-foreground"
    >
      <legend className="px-1 text-foreground">{t("settings.previewConditions")}</legend>
      <p id={hint} className="mb-2">
        {t("settings.previewOnly")}
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span>{t("settings.previewDaypart")}</span>
          <select
            className="h-9 rounded-lg border border-border bg-input px-2 text-foreground"
            value={signals.daypart ?? ""}
            onChange={(event) =>
              onChange({ ...signals, daypart: DAYPARTS.find((part) => part === event.target.value) })
            }
          >
            <option value="">{t("settings.previewNoDaypart")}</option>
            {DAYPARTS.map((part) => (
              <option key={part} value={part}>
                {t(`settings.preview.daypart.${part}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span>{t("settings.previewWeather")}</span>
          <select
            className="h-9 rounded-lg border border-border bg-input px-2 text-foreground"
            value={signals.weather ?? ""}
            onChange={(event) =>
              onChange({ ...signals, weather: WEATHERS.find((kind) => kind === event.target.value) ?? null })
            }
          >
            <option value="">{t("settings.previewNoWeather")}</option>
            {WEATHERS.map((kind) => (
              <option key={kind} value={kind}>
                {t(`settings.preview.weather.${kind}`)}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={!customized}
          className="h-9 rounded-lg border border-border px-3 hover:bg-accent disabled:opacity-50"
          onClick={() => onChange(null)}
        >
          {t("settings.previewNow")}
        </button>
      </div>
      <p className="mt-2">{weatherStatus}</p>
    </fieldset>
  );
}

type CardProps = {
  theme: SchemaThemeView;
  dark: boolean;
  signals: Signals;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
};

function ThemeCard({ theme, dark, signals, selected, disabled, onSelect }: CardProps) {
  // The plugin's name only when it says more than the title: "SF · sf" says nothing twice.
  const named = theme.plugin?.replace(/^heinzel-/, "");
  const plugin = named && named.toLowerCase() !== theme.title.toLowerCase() ? named : undefined;
  return (
    // biome-ignore lint/a11y/useSemanticElements: a card of a radio group, with a picture a radio input can't hold
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-label={plugin ? `${theme.title} · ${plugin}` : theme.title}
      disabled={disabled}
      onClick={onSelect}
      className={cn(
        "group flex flex-col overflow-hidden rounded-xl border border-border text-left transition-shadow hover:shadow-md disabled:opacity-60",
        selected && "ring-2 ring-primary ring-offset-2 ring-offset-popover",
      )}
    >
      <Miniature theme={theme} dark={dark} signals={signals} />
      <span className="flex items-baseline gap-2 border-t border-border px-3 py-2">
        <span className="truncate text-ui font-medium">{theme.title}</span>
        {plugin && <span className="truncate text-ui-sm text-muted-foreground">{plugin}</span>}
        {selected && <span className="ml-auto shrink-0 text-ui-sm text-primary">{t("settings.themeInUse")}</span>}
      </span>
    </button>
  );
}

/** The app in small: drawn at twice the size and scaled to half, so the theme's sizes keep their proportions. */
function Miniature({ theme, dark, signals }: { theme: SchemaThemeView; dark: boolean; signals: Signals }) {
  return (
    <span aria-hidden className="relative block aspect-[16/10] w-full overflow-hidden">
      <span
        data-testid={`theme-preview-${theme.name}`}
        style={previewStyle(theme, dark, signals)}
        className="absolute top-0 left-0 flex h-[200%] w-[200%] origin-top-left scale-50 bg-background text-ui text-foreground"
      >
        <span className="flex w-[30%] flex-col gap-1 border-r border-border bg-sidebar p-2">
          <span className="mb-1 flex h-9 items-center px-1">
            <Branding theme={theme} dark={dark} />
          </span>
          <span className="flex h-(--row) items-center gap-2 rounded-lg bg-accent px-2 text-accent-foreground">
            <Home className="size-(--icon) shrink-0 text-accent-foreground" />
            <span className="truncate">{t("nav.home")}</span>
          </span>
          <span className="flex h-(--row) items-center gap-2 rounded-lg px-2 text-foreground/90">
            <Search className="size-(--icon) shrink-0 text-icon" />
            <span className="truncate">{t("search.open")}</span>
          </span>
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex h-11 shrink-0 items-center border-b border-border px-4 font-medium">
            {t("today.title")}
          </span>
          <span className="flex flex-1 flex-col gap-3 bg-[linear-gradient(to_bottom,var(--color-main-tint),transparent_60%),var(--theme-backdrop)] p-4">
            <span className="flex flex-col gap-1 rounded-xl border border-border bg-card p-3">
              <span className="font-medium">{t("settings.previewCard")}</span>
              <span className="text-ui-sm text-muted-foreground">{t("settings.previewDetail")}</span>
            </span>
            <span className="flex items-center gap-2">
              <span className="flex h-9 items-center rounded-lg bg-primary px-3 font-medium text-primary-foreground">
                {t("settings.previewButton")}
              </span>
              <span className="flex h-9 min-w-0 flex-1 items-center rounded-lg border border-border bg-input px-3 text-muted-foreground">
                {t("chat.label")}
              </span>
            </span>
          </span>
        </span>
      </span>
    </span>
  );
}
