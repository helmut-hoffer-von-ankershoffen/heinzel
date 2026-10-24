// The editors a setting's schema picks (settingSchema.ts): each takes the value and hands back the next one, which
// the setting's row saves at once.
import { Plus, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { asRow, type Control, controlFor, initial, type JsonSchema } from "./settingSchema";
import { cityOf, TimeZonePicker } from "./TimeZonePicker";

const field =
  "h-8 rounded-lg border border-border bg-input px-2 text-sm outline-none focus:border-muted-foreground/50 disabled:opacity-50";

const pad = (value: number | string) => (typeof value === "number" && value < 10 ? `0${value}` : String(value));
/** Hours read as times: 9 → 09:00; other numbers as they are. */
const shown = (value: number | string, hours: boolean) => (hours ? `${pad(value)}:00` : String(value));

type Props = {
  id?: string;
  label: string;
  schema: JsonSchema;
  control?: Control;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
};

export function SchemaValue({ id, label, schema, control = controlFor(schema), value, disabled, onChange }: Props) {
  // Hours of a day (0–24) read as times.
  const hours = /hour/i.test(label);
  switch (control.kind) {
    case "choice":
      return (
        <select
          id={id}
          aria-label={id ? undefined : label}
          value={value === undefined || value === null ? "" : String(value)}
          disabled={disabled}
          onChange={(event) => {
            const picked = control.options.find((option) => String(option) === event.target.value);
            onChange(picked);
          }}
          className={field}
        >
          {(value === undefined || value === null) && <option value="">{t("settings.notSet")}</option>}
          {control.options.map((option) => (
            <option key={String(option)} value={String(option)}>
              {shown(option, hours)}
            </option>
          ))}
        </select>
      );
    case "number":
      return (
        <input
          id={id}
          aria-label={id ? undefined : label}
          type="number"
          step={control.integer ? 1 : "any"}
          min={control.min}
          max={control.max}
          defaultValue={typeof value === "number" ? value : ""}
          disabled={disabled}
          onBlur={(event) => {
            const next = event.target.value === "" ? null : Number(event.target.value);
            if (next !== value) onChange(next);
          }}
          className={cn(field, "w-24 text-right")}
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
      const choice = { kind: "choice", options: control.options } as const;
      return (
        <span className="flex items-center gap-1">
          <SchemaValue
            label={t("settings.from", { what: label })}
            schema={schema}
            control={choice}
            value={from}
            disabled={disabled}
            onChange={(next) => onChange([next, to])}
          />
          <span aria-hidden>–</span>
          <SchemaValue
            label={t("settings.to", { what: label })}
            schema={schema}
            control={choice}
            value={to}
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
  return (
    <div className="w-full">
      <ul className="flex flex-col gap-1.5">
        {rows.map((row, index) => {
          const name = rowName(row, control.columns) || String(index + 1);
          return (
            // Rows have no identity of their own; their place in the list is it.
            // biome-ignore lint/suspicious/noArrayIndexKey: see above
            <li key={index} className="flex flex-wrap items-center gap-2 rounded-lg border border-border/70 p-1.5">
              {control.columns.map(([key, schema]) => {
                const inner = controlFor(schema);
                return (
                  <span key={key} className={cn("min-w-0", inner.kind === "timezone" && "flex-1 basis-56")}>
                    {inner.kind === "text" ? (
                      <input
                        aria-label={`${titles(key, schema)} (${name})`}
                        defaultValue={row[key] === undefined ? "" : String(row[key])}
                        disabled={disabled}
                        onBlur={(event) =>
                          event.target.value !== String(row[key] ?? "") &&
                          onChange(
                            rows.map((other, at) => (at === index ? { ...other, [key]: event.target.value } : other)),
                          )
                        }
                        className={cn(field, "w-40")}
                      />
                    ) : (
                      <SchemaValue
                        label={`${titles(key, schema)} (${name})`}
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
              <button
                type="button"
                disabled={disabled}
                onClick={() => onChange(rows.filter((_, at) => at !== index))}
                aria-label={t("settings.removeItem", { item: name })}
                title={t("settings.removeItem", { item: name })}
                className="ml-auto flex size-8 items-center justify-center rounded-lg text-icon hover:bg-accent disabled:opacity-50"
              >
                <X aria-hidden className="size-4" />
              </button>
            </li>
          );
        })}
      </ul>
      <Button
        variant="outline"
        size="sm"
        className="mt-2"
        disabled={disabled}
        onClick={() => onChange([...rows, initial({ ...control.item, default: undefined })])}
        aria-label={t("settings.addTo", { what: label })}
      >
        <Plus aria-hidden />
        {t("settings.add")}
      </Button>
    </div>
  );
}
