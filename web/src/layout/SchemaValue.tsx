// The editors a setting's schema picks (settingSchema.ts): each takes the value and hands back the next one, which
// the setting's row saves at once. Nothing is typed that can be chosen (docs/SETTINGS_UX.md).
import { ArrowDown, ArrowUp, FolderOpen, KeyRound, MapPin, Plus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useCapabilities, useConfig, useInfo, useRevealSetting } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { type MessageKey, t } from "@/i18n";
import { cn } from "@/lib/utils";
import { zoneHour } from "@/lib/zoneTime";
import {
  asRow,
  type Control,
  controlFor,
  initial,
  type JsonSchema,
  ownZone,
  parseDuration,
  TIMEZONE,
} from "./settingSchema";
import { cityOf, TimeZonePicker } from "./TimeZonePicker";

const field =
  "h-8 rounded-lg border border-border bg-input px-2 text-sm outline-none focus:border-muted-foreground/50 disabled:opacity-50";

/** How an amount reads next to its field: "$" before dollars, the unit after anything else. */
const UNITS: Record<string, { before?: string; after?: MessageKey }> = {
  usd: { before: "$" },
  seconds: { after: "settings.unit.seconds" },
  minutes: { after: "settings.unit.minutes" },
  hours: { after: "settings.unit.hours" },
  days: { after: "settings.unit.days" },
  weeks: { after: "settings.unit.weeks" },
  "per hour": { after: "settings.unit.perHour" },
};

type Props = {
  id?: string;
  label: string;
  schema: JsonSchema;
  control?: Control;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
  /** The setting, as section.key: what Show in Finder opens for a path. */
  settingKey?: string;
  /** The time zone whose clock hours read in (a table row's zone); the viewer's own when not given. */
  zone?: string;
  /** Says whether what was entered is a value the setting takes, and if not what would be. */
  onInvalid?: (problem: string | null) => void;
};

export function SchemaValue({
  id,
  label,
  schema,
  control = controlFor(schema),
  value,
  disabled,
  onChange,
  settingKey,
  zone,
  onInvalid,
}: Props) {
  switch (control.kind) {
    case "choice": {
      // A value that is no longer a choice (a retired role) reads as not set, so a new one is picked.
      const offered = control.options.some((option) => String(option) === String(value));
      const clock = zone ?? ownZone();
      const name = (option: string | number) => {
        const label = control.labels?.[String(option)];
        if (label) return label;
        if (!control.hours || typeof option !== "number") return String(option);
        // The end of the day reads as midnight where 24 would read as the 12:00 am it starts with.
        const time = zoneHour(option, clock);
        return option === 24 && time === zoneHour(0, clock) ? t("settings.midnight", { time }) : time;
      };
      return (
        <select
          id={id}
          aria-label={id ? undefined : label}
          value={offered ? String(value) : ""}
          disabled={disabled}
          onChange={(event) => {
            const picked = control.options.find((option) => String(option) === event.target.value);
            onChange(picked);
          }}
          className={field}
        >
          {!offered && <option value="">{t("settings.notSet")}</option>}
          {control.options.map((option) => (
            <option key={String(option)} value={String(option)}>
              {name(option)}
            </option>
          ))}
        </select>
      );
    }
    case "number": {
      const unit = UNITS[control.unit ?? ""];
      return (
        <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
          {unit?.before && <span aria-hidden>{unit.before}</span>}
          <Draft
            id={id}
            label={id ? undefined : label}
            type="number"
            step={control.integer ? 1 : "any"}
            min={control.min}
            max={control.max}
            value={typeof value === "number" ? String(value) : ""}
            disabled={disabled}
            onCommit={(text) => {
              // An emptied field is no number: nothing to save (Reset gives the default back).
              if (text === "") return;
              const number = Number(text);
              const low =
                control.min !== undefined && (control.exclusiveMin ? number <= control.min : number < control.min);
              const high =
                control.max !== undefined && (control.exclusiveMax ? number >= control.max : number > control.max);
              if (Number.isNaN(number) || low || high || (control.integer && !Number.isInteger(number))) {
                onInvalid?.(numberProblem(control));
                return;
              }
              onInvalid?.(null);
              if (number !== value) onChange(number);
            }}
            className={cn(field, "w-24 text-right text-foreground")}
          />
          {unit?.after && <span>{t(unit.after)}</span>}
        </span>
      );
    }
    case "duration":
      return (
        <Duration id={id} label={label} value={value} disabled={disabled} onChange={onChange} onInvalid={onInvalid} />
      );
    case "path":
      return (
        <PathValue
          id={id}
          label={label}
          value={typeof value === "string" ? value : ""}
          settingKey={settingKey}
          disabled={disabled}
          onChange={onChange}
        />
      );
    case "secret":
      return <Secret id={id} value={value} />;
    case "options":
      return <Looked id={id} label={label} control={control} value={value} disabled={disabled} onChange={onChange} />;
    case "multi":
      return (
        <Multi
          label={label}
          source={control.source}
          capability={control.capability}
          value={Array.isArray(value) ? value.map(String) : []}
          disabled={disabled}
          onChange={onChange}
        />
      );
    case "text":
      return (
        <Draft
          id={id}
          label={id ? undefined : label}
          type={control.input}
          value={value === undefined || value === null ? "" : String(value)}
          placeholder={control.placeholder ?? t("settings.notSet")}
          disabled={disabled}
          onCommit={(text) => {
            const trimmed = text.trim();
            const problem = trimmed === "" ? null : textProblem(control.input, trimmed);
            onInvalid?.(problem);
            if (!problem) onChange(trimmed === "" ? null : trimmed);
          }}
          className={cn(field, "w-60 max-md:w-40")}
        />
      );
    case "timezone":
      return (
        <TimeZonePicker
          id={id}
          label={label}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          onChange={onChange}
        />
      );
    case "range": {
      const pair = Array.isArray(value) && value.length === 2 ? value : initial(schema);
      const [from, to] = pair as [number, number];
      const choice = { kind: "choice", options: control.options, hours: control.hours } as const;
      return (
        <span className="flex items-center gap-1">
          <SchemaValue
            label={t("settings.from", { what: label })}
            schema={schema}
            control={choice}
            value={from}
            zone={zone}
            disabled={disabled}
            onChange={(next) => onChange([next, to])}
          />
          <span aria-hidden>–</span>
          <SchemaValue
            label={t("settings.to", { what: label })}
            schema={schema}
            control={choice}
            value={to}
            zone={zone}
            disabled={disabled}
            onChange={(next) => onChange([from, next])}
          />
        </span>
      );
    }
    case "chips":
      return (
        <Chips
          label={label}
          value={Array.isArray(value) ? value.map(String) : []}
          disabled={disabled}
          onChange={onChange}
        />
      );
    case "table":
      return (
        <Table
          label={label}
          control={control}
          value={Array.isArray(value) ? value : []}
          disabled={disabled}
          onChange={onChange}
        />
      );
    default:
      return null;
  }
}

/** What a number must be, in words: "a whole number from 1 to 600", "a number more than 0". */
function numberProblem(control: Extract<Control, { kind: "number" }>): string {
  const what = control.integer ? t("settings.wholeNumber") : t("settings.number");
  if (control.exclusiveMin && control.min !== undefined) {
    const above = t("settings.moreThan", { what, min: control.min });
    return control.max === undefined ? above : `${above} ${t("settings.andAtMost", { max: control.max })}`;
  }
  if (control.min !== undefined && control.max !== undefined) {
    return t("settings.between", { what, min: control.min, max: control.max });
  }
  if (control.min !== undefined) return t("settings.atLeast", { what, min: control.min });
  if (control.max !== undefined) return t("settings.atMost", { what, max: control.max });
  return t("settings.want", { what });
}

/** Why `text` isn't a web address or an email address, with what would be; null when it is. */
export function textProblem(input: "text" | "url" | "email", text: string): string | null {
  if (input === "url") {
    try {
      const url = new URL(text);
      if ((url.protocol === "https:" || url.protocol === "http:") && url.hostname) return null;
    } catch {
      // not an address at all
    }
    return t("settings.wantUrl");
  }
  if (input === "email" && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(text)) return t("settings.wantEmail");
  return null;
}

const DURATION_UNITS = ["s", "m", "h", "d"] as const;

/** A duration as a number and a unit, stored as `90m`, `36h` or `2d`. */
function Duration({
  id,
  label,
  value,
  disabled,
  onChange,
  onInvalid,
}: {
  id?: string;
  label: string;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
  onInvalid?: (problem: string | null) => void;
}) {
  const parsed = parseDuration(value) ?? { amount: 1, unit: "h" as const };
  return (
    <span className="flex items-center gap-1.5">
      <Draft
        id={id}
        label={id ? undefined : label}
        type="number"
        step={1}
        min={1}
        value={String(parsed.amount)}
        disabled={disabled}
        onCommit={(text) => {
          const amount = Number(text);
          if (!Number.isInteger(amount) || amount < 1) {
            onInvalid?.(t("settings.moreThan", { what: t("settings.wholeNumber"), min: 0 }));
            return;
          }
          onInvalid?.(null);
          if (`${amount}${parsed.unit}` !== value) onChange(`${amount}${parsed.unit}`);
        }}
        className={cn(field, "w-20 text-right")}
      />
      <select
        aria-label={t("settings.unitOf", { what: label })}
        value={parsed.unit}
        disabled={disabled}
        onChange={(event) => onChange(`${parsed.amount}${event.target.value}`)}
        className={field}
      >
        {DURATION_UNITS.map((unit) => (
          <option key={unit} value={unit}>
            {t(`settings.duration.${unit}` as MessageKey)}
          </option>
        ))}
      </select>
    </span>
  );
}

/** A file or folder: its path, and Show in Finder (or the desktop's file manager) when hz can open one here. */
function PathValue({
  id,
  label,
  value,
  settingKey,
  disabled,
  onChange,
}: {
  id?: string;
  label: string;
  value: string;
  settingKey?: string;
  disabled: boolean;
  onChange: (value: unknown) => void;
}) {
  const config = useConfig();
  const reveal = useRevealSetting();
  const can = Boolean(config.data?.can_reveal && settingKey && value);
  const finder = config.data?.file_manager === "Finder" ? t("settings.showInFinder") : t("settings.openFolder");
  return (
    <span className="flex flex-col items-end gap-1">
      <span className="flex items-center gap-1.5">
        <Draft
          id={id}
          label={id ? undefined : label}
          value={value}
          placeholder={t("settings.notSet")}
          disabled={disabled}
          onCommit={(text) => onChange(text.trim() === "" ? null : text.trim())}
          className={cn(field, "w-60 font-mono text-xs max-md:w-40")}
        />
        {can && settingKey && (
          <Button
            variant="outline"
            size="sm"
            disabled={reveal.isPending}
            onClick={() => reveal.mutate(settingKey)}
            aria-label={`${finder}: ${label}`}
            title={finder}
          >
            <FolderOpen aria-hidden />
          </Button>
        )}
      </span>
      {(reveal.error || (reveal.data && !reveal.data.opened)) && (
        <span role="alert" className="text-xs text-warn">
          {reveal.error?.message ?? reveal.data?.message}
        </span>
      )}
    </span>
  );
}

/** The names an `x-options` source stands for, looked up here: the installed modules, or a capability's adapters. */
function useOptions(source: string, capability?: string): string[] {
  const info = useInfo();
  const capabilities = useCapabilities();
  if (source === "modules") return (info.data?.modules ?? []).map((module) => module.qualname);
  return (capabilities.data?.adapters ?? [])
    .filter((adapter) => !capability || adapter.capability === capability)
    .map((adapter) => adapter.adapter);
}

/** One of the values a source names, e.g. the adapter that serves a capability; not set lets hz choose. */
function Looked({
  id,
  label,
  control,
  value,
  disabled,
  onChange,
}: {
  id?: string;
  label: string;
  control: Extract<Control, { kind: "options" }>;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
}) {
  const found = useOptions(control.source, control.capability);
  const current = typeof value === "string" ? value : "";
  const options = [...new Set([...found, ...(control.none ? [control.none] : []), ...(current ? [current] : [])])];
  return (
    <select
      id={id}
      aria-label={id ? undefined : label}
      value={current}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}
      className={field}
    >
      <option value="">{t("settings.automatic")}</option>
      {options.map((option) => (
        <option key={option} value={option}>
          {option === control.none ? t("settings.noAdapter") : option}
        </option>
      ))}
    </select>
  );
}

/** A secret: whether it is set, never its value, and never a box to type it into. */
function Secret({ id, value }: { id?: string; value: unknown }) {
  const set = typeof value === "string" && value !== "";
  return (
    <span id={id} className="flex items-center gap-1.5 text-sm text-muted-foreground">
      <KeyRound aria-hidden className="size-4" />
      {set ? t("settings.secretSet") : t("settings.secretNotSet")}
    </span>
  );
}

/** Several of the installed modules, a checkbox each: nobody types a module's qualified name. */
function Multi({
  label,
  source,
  capability,
  value,
  disabled,
  onChange,
}: {
  label: string;
  source: string;
  capability?: string;
  value: string[];
  disabled: boolean;
  onChange: (value: string[]) => void;
}) {
  const known = useOptions(source, capability);
  const options = [...new Set([...known, ...value])].sort();
  return (
    <fieldset className="flex max-h-48 flex-wrap gap-x-4 gap-y-1 overflow-y-auto">
      <legend className="sr-only">{label}</legend>
      {options.map((name) => (
        <label key={name} className="flex items-center gap-1.5 font-mono text-xs">
          <input
            type="checkbox"
            checked={value.includes(name)}
            disabled={disabled}
            onChange={(event) =>
              onChange(event.target.checked ? [...value, name] : value.filter((other) => other !== name))
            }
          />
          {name}
        </label>
      ))}
    </fieldset>
  );
}

/** A list of words: one chip each with ×, and a field that adds one on Enter. */
function Chips({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: string[];
  disabled: boolean;
  onChange: (value: string[]) => void;
}) {
  const [text, setText] = useState("");
  const add = () => {
    const word = text.trim();
    if (word && !value.includes(word)) onChange([...value, word]);
    setText("");
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {value.map((word) => (
        <span
          key={word}
          className="flex items-center gap-1 rounded-full border border-border bg-muted py-0.5 pr-1 pl-2.5 text-sm"
        >
          {word}
          <button
            type="button"
            disabled={disabled}
            onClick={() => onChange(value.filter((other) => other !== word))}
            aria-label={t("settings.removeItem", { item: word })}
            className="flex size-5 items-center justify-center rounded-full text-icon hover:bg-accent"
          >
            <X aria-hidden className="size-3.5" />
          </button>
        </span>
      ))}
      <input
        value={text}
        disabled={disabled}
        aria-label={t("settings.addTo", { what: label })}
        placeholder={t("settings.addItem")}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            add();
          }
        }}
        onBlur={add}
        className={cn(field, "w-36")}
      />
    </div>
  );
}

/** What a row is called, for its remove button: its time zone's city, or its first value. */
function rowName(row: Record<string, unknown>, columns: [string, JsonSchema][]): string {
  const [key, schema] = columns[0] ?? ["", {}];
  const first = row[key];
  if (schema.format === "iana-timezone" && typeof first === "string") return cityOf(first);
  return first === undefined ? "" : String(first);
}

/** A list of tables: one row per item with a control per property, × to remove it, and Add for a new one. */
type DraftProps = {
  id?: string;
  label?: string;
  value: string;
  disabled: boolean;
  onCommit: (text: string) => void;
  className: string;
  type?: "text" | "number" | "url" | "email";
  placeholder?: string;
  step?: number | "any";
  min?: number;
  max?: number;
};

/** A field edited in place and saved when left or on Enter; it shows the value again whenever that changes. */
function Draft({
  id,
  label,
  value,
  disabled,
  onCommit,
  className,
  type = "text",
  step,
  min,
  max,
  placeholder,
}: DraftProps) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <input
      id={id}
      aria-label={label}
      type={type}
      step={step}
      min={min}
      max={max}
      placeholder={placeholder}
      value={text}
      disabled={disabled}
      onChange={(event) => setText(event.target.value)}
      onBlur={() => text !== value && onCommit(text)}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        if (event.key === "Escape") {
          event.stopPropagation();
          setText(value);
        }
      }}
      className={className}
    />
  );
}

const rowButton =
  "flex size-8 items-center justify-center rounded-lg text-icon hover:bg-accent disabled:opacity-40 [&_svg]:size-4";

/** A list of tables: one row per item with a control per property, buttons to move or remove it, and Add. The order
 *  means something: in a list of time zones the first is yours ("Primary"). */
function Table({
  label,
  control,
  value,
  disabled,
  onChange,
}: {
  label: string;
  control: Extract<Control, { kind: "table" }>;
  value: unknown[];
  disabled: boolean;
  onChange: (value: unknown[]) => void;
}) {
  const rows = value.map((item) => asRow(item, control.item));
  const titles = (key: string, schema: JsonSchema) => schema.title ?? key;
  const [first, firstSchema] = control.columns[0] ?? ["", {}];
  const zones = firstSchema.format === TIMEZONE;
  const mine = ownZone();
  const missing = zones && !rows.some((row) => row[first] === mine);
  const fresh = () => initial({ ...control.item, default: undefined }) as Record<string, unknown>;
  const move = (from: number, to: number) => {
    const next = [...rows];
    const [moved] = next.splice(from, 1);
    if (moved) next.splice(to, 0, moved);
    onChange(next);
  };
  return (
    <div className="w-full">
      <ul className="flex flex-col gap-1.5">
        {rows.map((row, index) => {
          const name = rowName(row, control.columns) || String(index + 1);
          return (
            // A row's place is its identity (the order means something); its fields show what it holds now.
            // biome-ignore lint/suspicious/noArrayIndexKey: see above
            <li key={index} className="flex flex-wrap items-center gap-2 rounded-lg border border-border/70 p-1.5">
              {control.columns.map(([key, schema]) => {
                const inner = controlFor(schema);
                return (
                  <span key={key} className={cn("min-w-0", inner.kind === "timezone" && "flex-1 basis-56")}>
                    {inner.kind === "text" ? (
                      <Draft
                        label={`${titles(key, schema)} (${name})`}
                        value={row[key] === undefined ? "" : String(row[key])}
                        disabled={disabled}
                        onCommit={(text) =>
                          onChange(rows.map((other, at) => (at === index ? { ...other, [key]: text } : other)))
                        }
                        className={cn(field, "w-40")}
                      />
                    ) : (
                      <SchemaValue
                        label={`${titles(key, schema)} (${name})`}
                        zone={zones && typeof row[first] === "string" ? (row[first] as string) : undefined}
                        schema={schema}
                        control={inner}
                        value={row[key] ?? initial(schema)}
                        disabled={disabled}
                        onChange={(next) =>
                          onChange(rows.map((other, at) => (at === index ? { ...other, [key]: next } : other)))
                        }
                      />
                    )}
                  </span>
                );
              })}
              {zones && (
                // Every row keeps the badge's column, so the first row's pickers are as wide as the others'.
                <span className="flex w-16 shrink-0 justify-center">
                  {index === 0 && (
                    <span className="rounded-md border border-primary/40 px-1.5 text-[11px] text-primary">
                      {t("settings.primary")}
                    </span>
                  )}
                </span>
              )}
              <span className="ml-auto flex items-center">
                <button
                  type="button"
                  disabled={disabled || index === 0}
                  onClick={() => move(index, index - 1)}
                  aria-label={t("settings.moveUp", { item: name })}
                  title={t("settings.moveUp", { item: name })}
                  className={rowButton}
                >
                  <ArrowUp aria-hidden />
                </button>
                <button
                  type="button"
                  disabled={disabled || index === rows.length - 1}
                  onClick={() => move(index, index + 1)}
                  aria-label={t("settings.moveDown", { item: name })}
                  title={t("settings.moveDown", { item: name })}
                  className={rowButton}
                >
                  <ArrowDown aria-hidden />
                </button>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onChange(rows.filter((_, at) => at !== index))}
                  aria-label={t("settings.removeItem", { item: name })}
                  title={t("settings.removeItem", { item: name })}
                  className={rowButton}
                >
                  <X aria-hidden />
                </button>
              </span>
            </li>
          );
        })}
      </ul>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => onChange([...rows, fresh()])}
          aria-label={t("settings.addTo", { what: label })}
        >
          <Plus aria-hidden />
          {t("settings.add")}
        </Button>
        {missing && (
          <Button
            variant="outline"
            size="sm"
            disabled={disabled}
            onClick={() => onChange([{ ...fresh(), [first]: mine }, ...rows])}
          >
            <MapPin aria-hidden />
            {t("settings.useMyZone", { zone: cityOf(mine) })}
          </Button>
        )}
      </div>
    </div>
  );
}
