// A time zone chosen by searching the IANA names the browser knows: each shows its city, its name and its current UTC
// offset ("Berlin · Europe/Berlin · UTC+2"). A combobox: type to filter, arrows to move, Enter to take, Escape to close.
import { ChevronDown } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

/** `America/Los_Angeles` → `Los Angeles`. */
export const cityOf = (zone: string) => (zone.split("/").at(-1) ?? zone).replaceAll("_", " ");

/** Every IANA zone this browser knows; just `fallback` where it can't list them. */
export function allZones(fallback: string[] = []): string[] {
  const list = (Intl as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];
  return [...new Set([...list, ...fallback.filter(Boolean)])];
}

/** `UTC+2`, `UTC−5`, `UTC+5:30` now, or "" for a name the browser doesn't know. */
export function offsetOf(zone: string, at = new Date()): string {
  try {
    const name =
      new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "shortOffset" })
        .formatToParts(at)
        .find((part) => part.type === "timeZoneName")?.value ?? "";
    return name.replace(/^GMT/, "UTC").replace("-", "−") || "UTC";
  } catch {
    return "";
  }
}

export const zoneLabel = (zone: string) => [cityOf(zone), zone, offsetOf(zone)].filter(Boolean).join(" · ");

const SHOWN = 60;

type Props = { id?: string; value: string; disabled?: boolean; label: string; onChange: (zone: string) => void };

export function TimeZonePicker({ id, value, disabled, label, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const listId = useId();
  const zones = useMemo(() => allZones([value]), [value]);
  const needle = query.trim().toLowerCase().replaceAll(" ", "_");
  const matches = useMemo(
    () => (needle ? zones.filter((zone) => zone.toLowerCase().includes(needle)) : zones).slice(0, SHOWN),
    [zones, needle],
  );
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  const take = (zone: string | undefined) => {
    if (!zone) return;
    setOpen(false);
    setQuery("");
    if (zone !== value) onChange(zone);
  };
  return (
    <div ref={ref} className="relative min-w-0">
      <button
        id={id}
        type="button"
        disabled={disabled}
        onClick={() => {
          setOpen((current) => !current);
          setActive(0);
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${value ? zoneLabel(value) : t("settings.notSet")}`}
        className="flex h-8 w-full min-w-0 items-center gap-1.5 rounded-lg border border-border bg-input px-2.5 text-left text-sm disabled:opacity-50"
      >
        <span className="truncate">{value ? zoneLabel(value) : t("settings.notSet")}</span>
        <ChevronDown aria-hidden className="ml-auto size-4 shrink-0 text-icon" />
      </button>
      {open && (
        <div className="absolute top-full left-0 z-40 mt-1 w-72 rounded-xl border border-border bg-popover p-1.5 shadow-xl">
          <input
            role="combobox"
            aria-expanded
            aria-controls={listId}
            aria-activedescendant={matches[active] ? `${listId}-${active}` : undefined}
            aria-label={t("zones.search")}
            // biome-ignore lint/a11y/noAutofocus: the list opened to be searched
            autoFocus
            value={query}
            placeholder={t("zones.search")}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActive((index) => Math.min(index + 1, matches.length - 1));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActive((index) => Math.max(index - 1, 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                take(matches[active]);
              } else if (event.key === "Escape") {
                event.stopPropagation();
                setOpen(false);
              }
            }}
            className="h-8 w-full rounded-lg bg-input px-2.5 text-sm outline-none focus:ring-1 focus:ring-ring"
          />
          <div id={listId} role="listbox" aria-label={label} className="mt-1 max-h-64 overflow-y-auto">
            {matches.map((zone, index) => (
              <div
                key={zone}
                id={`${listId}-${index}`}
                role="option"
                tabIndex={-1}
                aria-selected={index === active}
                onMouseEnter={() => setActive(index)}
                onClick={() => take(zone)}
                onKeyDown={() => undefined}
                className={cn(
                  "flex cursor-pointer items-baseline gap-2 rounded-lg px-2.5 py-1.5 text-sm",
                  index === active && "bg-accent",
                  zone === value && "font-medium",
                )}
              >
                <span className="truncate">{cityOf(zone)}</span>
                <span className="truncate text-xs text-muted-foreground">{zone}</span>
                <span className="ml-auto shrink-0 text-xs text-muted-foreground">{offsetOf(zone)}</span>
              </div>
            ))}
            {matches.length === 0 && <p className="px-2.5 py-1.5 text-sm text-muted-foreground">{t("zones.none")}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
