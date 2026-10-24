// The control of one setting in Settings, chosen by its JSON Schema (`hz system config show`): a switch, a list of
// choices, a number, a time zone picker, chips for a list of words, a table for a list of tables, or text. A change
// is saved at once with `hz system config set` as TOML; the server's validation error shows under the setting.
import { useEffect, useId, useState } from "react";
import type { SchemaSettingInfo } from "@/api/client";
import { useSetSetting } from "@/api/queries";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { SchemaValue } from "./SchemaValue";
import { controlFor, type JsonSchema, toToml } from "./settingSchema";

const label = (key: string) => (key.charAt(0).toUpperCase() + key.slice(1)).replaceAll("_", " ");

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
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function Row({ setting }: { setting: SchemaSettingInfo }) {
  const save = useSetSetting();
  const key = `${setting.section}.${setting.key}`;
  const fromEnv = setting.source === "env";
  const ownValue = setting.source === "user" || setting.source === "project";
  const value = setting.value ?? setting.default;
  const inputId = useId();
  const schema = (setting.json_schema ?? null) as JsonSchema | null;
  const control = controlFor(schema);
  // Lists get the row's whole width, under the label; single values sit on its right.
  const wide = control.kind === "table" || control.kind === "chips";
  const disabled = fromEnv || save.isPending;
  const commit = (next: unknown) => save.mutate({ key, value: next === null ? null : toToml(next) });
  const editor =
    typeof value === "boolean" || control.kind === "switch" ? (
      <Switch
        id={inputId}
        on={value === true}
        disabled={disabled}
        onToggle={() => save.mutate({ key, value: String(!value) })}
      />
    ) : schema && control.kind !== "text" ? (
      <SchemaValue
        id={wide ? undefined : inputId}
        label={label(setting.key)}
        schema={schema}
        control={control}
        value={value}
        disabled={disabled}
        onChange={commit}
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
        onCommit={(text) => save.mutate({ key, value: text === "" ? null : text })}
      />
    );
  const reset = ownValue && !fromEnv && (
    <button
      type="button"
      onClick={() => save.mutate({ key, value: null })}
      className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
    >
      {t("settings.reset")}
    </button>
  );
  return (
    <li className="border-b border-border/70 py-4 last:border-b-0">
      <div className="flex items-start gap-6">
        <div className="min-w-0 flex-1">
          {wide ? (
            <p id={inputId} className="text-ui">
              {label(setting.key)}
            </p>
          ) : (
            <label htmlFor={inputId} className="text-ui">
              {label(setting.key)}
            </label>
          )}
          <p className="mt-0.5 text-sm text-muted-foreground">{setting.description}</p>
          {fromEnv && <p className="mt-1 text-xs text-warn">{t("settings.fromEnv", { env: setting.env })}</p>}
          {save.error && (
            <p role="alert" className="mt-1 text-xs text-bad">
              {save.error.message}
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
