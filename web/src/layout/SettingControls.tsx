// The control of one setting in Settings, chosen by its JSON Schema (`hz system config show`; docs/SETTINGS_UX.md):
// a switch, labelled choices, a number with its unit, a duration, a time zone picker, a path with Show in Finder, a
// secret's state, checkboxes, chips, a table, or text. A change is saved at once with `hz system config set` as TOML,
// with Undo for a few seconds and Reset to the default it names; a value refused here or by the server says why
// under the setting.
import { useEffect, useId, useState } from "react";
import type { SchemaSettingInfo } from "@/api/client";
import { useSetSetting } from "@/api/queries";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { SchemaValue } from "./SchemaValue";
import { controlFor, type JsonSchema, labelOf, toToml, unwrap } from "./settingSchema";

type SwitchProps = { id?: string; labelledBy?: string; on: boolean; disabled: boolean; onToggle: () => void };

export function Switch({ id, labelledBy, on, disabled, onToggle }: SwitchProps) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={on}
      aria-labelledby={labelledBy}
      disabled={disabled}
      onClick={onToggle}
      className={cn(
        "relative h-6 w-10 shrink-0 rounded-full transition-colors disabled:opacity-50",
        on ? "bg-switch" : "bg-accent",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow transition-transform",
          on && "translate-x-4",
        )}
      />
    </button>
  );
}

/** The text `hz system config set` takes for a value: TOML for numbers, booleans and lists, a string as it is. */
function asConfig(value: unknown): string {
  return typeof value === "string" ? value : toToml(value);
}

/** What a value reads as next to "Default:": a choice by its label, an hour as a time, a list joined. */
function readable(value: unknown, schema: JsonSchema | null): string {
  if (value === null || value === undefined || value === "") return t("settings.notSet");
  const labels = unwrap(schema ?? undefined)?.["x-labels"];
  if (labels && typeof value === "string" && labels[value]) return labels[value];
  if (typeof value === "boolean") return value ? t("settings.on") : t("settings.off");
  if (Array.isArray(value)) {
    if (value.length === 0) return t("settings.emptyList");
    if (value.every((item) => typeof item !== "object")) return value.join(", ");
  }
  return typeof value === "object" ? "" : String(value);
}

/** How long Undo stays after a change. */
export const UNDO_MS = 8000;
/** Told when a row offers Undo, so no other row's offer stays under it. */
const UNDO_EVENT = "hz:settings-undo";

export function Row({ setting }: { setting: SchemaSettingInfo }) {
  const save = useSetSetting();
  const key = `${setting.section}.${setting.key}`;
  const fromEnv = setting.source === "env";
  const ownValue = setting.source === "user" || setting.source === "project";
  const value = setting.value ?? setting.default;
  const inputId = useId();
  const schema = (setting.json_schema ?? null) as JsonSchema | null;
  const control = controlFor(schema);
  const name = labelOf(setting.key, schema);
  const [problem, setProblem] = useState<string | null>(null);
  // The value before the last change, to put back with Undo; `unset` when it was the default.
  const [undo, setUndo] = useState<{ before: unknown; unset: boolean; reset: boolean } | null>(null);
  useEffect(() => {
    if (!undo) return;
    const timer = window.setTimeout(() => setUndo(null), UNDO_MS);
    // One Undo at a time: a change in another row takes it over.
    const other = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== key) setUndo(null);
    };
    window.addEventListener(UNDO_EVENT, other);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener(UNDO_EVENT, other);
    };
  }, [undo, key]);
  // A secret's value never reaches the page (only a mask does), so it can't be put back: no Undo, no Reset.
  const secret = control.kind === "secret";
  // Lists get the row's whole width, under the label; single values sit on its right.
  const wide = control.kind === "table" || control.kind === "chips" || control.kind === "multi";
  const disabled = fromEnv || save.isPending;
  /** Saves `next` (TOML of it; `raw` text as typed, which the server reads as TOML or a string), remembering what
   *  was there for Undo. */
  const write = (next: unknown, remember = true, raw = false) => {
    if (remember && !secret) {
      setUndo({ before: setting.value, unset: setting.source !== "user", reset: next === null });
      window.dispatchEvent(new CustomEvent(UNDO_EVENT, { detail: key }));
    }
    setProblem(null);
    save.mutate({ key, value: next === null ? null : raw ? String(next) : toToml(next) });
  };
  const editor =
    control.kind === "switch" || typeof value === "boolean" ? (
      <Switch id={inputId} on={value === true} disabled={disabled} onToggle={() => write(!value)} />
    ) : schema ? (
      <SchemaValue
        id={wide ? undefined : inputId}
        label={name}
        schema={schema}
        control={control}
        value={value}
        disabled={disabled}
        onChange={write}
        settingKey={key}
        onInvalid={setProblem}
      />
    ) : (
      <TextValue
        id={inputId}
        value={setting.value === null || setting.value === undefined ? "" : asConfig(setting.value)}
        placeholder={
          setting.default === null || setting.default === undefined ? t("settings.notSet") : asConfig(setting.default)
        }
        numeric={typeof value === "number"}
        disabled={disabled}
        onCommit={(text) => write(text === "" ? null : text, true, true)}
      />
    );
  const fallback = readable(setting.default, schema);
  const reset = ownValue && !fromEnv && !secret && (
    <button
      type="button"
      onClick={() => write(null)}
      title={fallback ? t("settings.resetTo", { value: fallback }) : undefined}
      className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
    >
      {t("settings.reset")}
    </button>
  );
  const error = problem ?? save.error?.message;
  return (
    <li className="border-b border-border/70 py-4 last:border-b-0">
      <div className="flex items-start gap-6">
        <div className="min-w-0 flex-1">
          {wide || secret ? (
            <p id={inputId} className="text-ui">
              {name}
            </p>
          ) : (
            <label htmlFor={inputId} className="text-ui">
              {name}
            </label>
          )}
          <p className="mt-0.5 text-sm text-muted-foreground">{setting.description}</p>
          {ownValue && !secret && fallback && (
            <p className="mt-0.5 text-xs text-muted-foreground">{t("settings.defaultIs", { value: fallback })}</p>
          )}
          {fromEnv && <p className="mt-1 text-xs text-warn">{t("settings.fromEnv", { env: setting.env })}</p>}
          {secret && !fromEnv && (
            <p className="mt-0.5 text-xs text-muted-foreground">{t("settings.secretHow", { env: setting.env })}</p>
          )}
          {error && (
            <p role="alert" className="mt-1 text-xs text-bad">
              {error}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-0.5">
          {reset}
          {!wide && editor}
        </div>
      </div>
      {wide && (
        // biome-ignore lint/a11y/useSemanticElements: a group of controls named by the setting's label
        <div role="group" aria-labelledby={inputId} className="mt-3">
          {editor}
        </div>
      )}
      {undo && !save.isPending && !save.error && (
        <div
          role="status"
          className="fixed bottom-6 left-1/2 z-[60] flex -translate-x-1/2 items-center gap-3 rounded-lg border border-border bg-popover px-4 py-2 text-sm shadow-lg"
        >
          {undo.reset ? t("settings.reverted", { what: name }) : t("settings.changed", { what: name })}
          <button
            type="button"
            onClick={() => {
              const { before, unset } = undo;
              setUndo(null);
              write(unset ? null : before, false);
            }}
            className="rounded-md px-2 py-0.5 font-medium text-primary hover:bg-accent"
          >
            {t("settings.undo")}
          </button>
        </div>
      )}
    </li>
  );
}

type TextValueProps = {
  id: string;
  value: string;
  placeholder: string;
  numeric: boolean;
  disabled: boolean;
  onCommit: (text: string) => void;
};

/** A value edited in place and saved on Enter or when leaving the field; Escape restores it. */
function TextValue({ id, value, placeholder, numeric, disabled, onCommit }: TextValueProps) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => {
    if (text.trim() !== value) onCommit(text.trim());
  };
  return (
    <input
      id={id}
      value={text}
      inputMode={numeric ? "decimal" : undefined}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(event) => setText(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        if (event.key === "Escape") {
          event.stopPropagation();
          setText(value);
        }
      }}
      className={cn(
        "h-8 rounded-lg border border-border bg-input px-2.5 text-sm outline-none placeholder:text-muted-foreground focus:border-muted-foreground/50 disabled:opacity-50",
        numeric ? "w-24 text-right" : "w-60 max-md:w-40",
      )}
    />
  );
}
