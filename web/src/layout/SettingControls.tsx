// The control of one setting in Settings, chosen by its JSON Schema (`hz system config show`): a switch, a list of
// choices, a number, a time zone picker, chips for a list of words, a table for a list of tables, or text. A change
// is saved at once with `hz system config set` as TOML; the server's validation error shows under the setting.
import { useMutation } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { api, type SchemaSettingInfo, unwrap as unwrapResponse } from "@/api/client";
import { useConfig, useSetSetting } from "@/api/queries";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { RestartAndApply } from "@/lifecycle/RestartAndApply";
import { SchemaValue } from "./SchemaValue";
import { controlFor, type JsonSchema, toToml, unwrap } from "./settingSchema";
import { inWords } from "./settingsSections";

const label = inWords;

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

export function Row({ setting }: { setting: SchemaSettingInfo }) {
  const save = useSetSetting();
  const [staged, setStaged] = useState<unknown | undefined>(undefined);
  const [editorVersion, setEditorVersion] = useState(0);
  const key = `${setting.section}.${setting.key}`;
  const fromEnv = setting.source === "env";
  const ownValue = setting.source === "user";
  const value = setting.value ?? setting.default;
  const inputId = useId();
  const schema = (setting.json_schema ?? null) as JsonSchema | null;
  const title = unwrap(schema ?? undefined)?.title ?? label(setting.key);
  const control = controlFor(schema);
  const unit = unwrap(schema ?? undefined)?.["x-unit"];
  const secret = control.kind === "secret";
  // Lists get the row's whole width, under the label; single values sit on its right.
  const wide =
    control.kind === "table" || control.kind === "chips" || (control.kind === "installed" && control.multiple);
  const disabled = fromEnv || save.isPending;
  const commit = (next: unknown) => setStaged(next);
  const editor = secret ? (
    <SecretValue id={inputId} disabled={disabled} onCommit={commit} />
  ) : typeof value === "boolean" || control.kind === "switch" ? (
    <Switch id={inputId} on={value === true} disabled={disabled} onToggle={() => commit(!value)} />
  ) : schema && control.kind !== "text" ? (
    <SchemaValue
      id={wide ? undefined : inputId}
      label={title}
      schema={schema}
      control={control}
      value={value}
      disabled={disabled}
      onChange={commit}
    />
  ) : (
    <TextValue
      key={editorVersion}
      id={inputId}
      value={setting.value === null || setting.value === undefined ? "" : asConfig(setting.value)}
      placeholder={
        setting.default === null || setting.default === undefined ? t("settings.notSet") : asConfig(setting.default)
      }
      numeric={typeof value === "number"}
      disabled={disabled}
      onCommit={(text) => commit(text === "" ? null : text)}
    />
  );
  const reset = ownValue && !fromEnv && (
    <button
      type="button"
      disabled={disabled}
      title="Remove your user override"
      onClick={() => commit(null)}
      className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
    >
      {t("settings.reset")}
    </button>
  );
  const serializedStaged =
    staged === null ? null : control.kind === "text" && typeof staged === "string" ? staged : toToml(staged);
  const persistStaged = async () => {
    await save.mutateAsync({ key, value: serializedStaged });
  };
  return (
    <li className="border-b border-border/70 py-4 last:border-b-0">
      <div className="flex flex-wrap items-start gap-x-6 gap-y-2 max-sm:flex-col">
        <div className="min-w-0 flex-[1_1_16rem] max-sm:flex-auto">
          {wide ? (
            <p id={inputId} className="text-ui">
              {title}
            </p>
          ) : (
            <label htmlFor={inputId} className="text-ui">
              {title}
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
        <div className="flex min-w-0 max-w-full shrink-0 flex-wrap items-center gap-2 pt-0.5 max-sm:w-full">
          {reset}
          {!wide && editor}
          {control.kind === "path" && <PathReveal settingKey={key} value={value} saving={save.isPending} />}
          {unit && unit !== "hour-of-day" && <span className="text-sm text-muted-foreground">{unit}</span>}
        </div>
      </div>
      {wide && (
        // biome-ignore lint/a11y/useSemanticElements: a group of controls named by the setting's label
        <div role="group" aria-labelledby={inputId} className="mt-3">
          {editor}
        </div>
      )}
      {staged !== undefined && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-border p-3">
          <RestartAndApply
            target="hz ui"
            changes={[title]}
            // Secret values are intentionally excluded: the backend never exposes them in its loaded-settings proof.
            settings={
              secret
                ? []
                : [
                    {
                      section: setting.section,
                      key: setting.key,
                      value: staged === null ? (setting.default ?? null) : normalizeSettingValue(staged, schema),
                    },
                  ]
            }
            preserve={async () => {
              await save.mutateAsync({ key, value: serializedStaged });
              return true;
            }}
            onUnsupported={persistStaged}
            deferredMessage={`Saved ${title}. This server cannot restart hz ui. Run \`hz ui\` in a terminal to apply this setting.`}
            onApplied={() => {
              setStaged(undefined);
              setEditorVersion((version) => version + 1);
            }}
          />
          <button
            type="button"
            disabled={save.isPending}
            onClick={() => {
              setStaged(undefined);
              setEditorVersion((version) => version + 1);
            }}
            className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            {t("lifecycle.cancel")}
          </button>
          {save.error && (
            <span role="alert" className="text-xs text-bad">
              {save.error.message}
            </span>
          )}
        </div>
      )}
    </li>
  );
}

function normalizeSettingValue(value: unknown, schema: JsonSchema | null): unknown {
  const type = unwrap(schema ?? undefined)?.type;
  if (typeof value === "string" && (type === "integer" || type === "number")) {
    const number = Number(value);
    return Number.isFinite(number) ? number : value;
  }
  return value;
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

/** A secret report contains a mask, never an editable replacement value. Save only an explicit new entry. */
function SecretValue({ id, disabled, onCommit }: { id: string; disabled: boolean; onCommit: (text: string) => void }) {
  const [text, setText] = useState("");
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (text) {
          onCommit(text);
          setText("");
        }
      }}
      className="flex items-center gap-2"
    >
      <input
        id={id}
        type="password"
        autoComplete="new-password"
        value={text}
        disabled={disabled}
        placeholder="Enter a replacement"
        onChange={(event) => setText(event.target.value)}
        className="h-8 w-40 rounded-lg border border-border bg-input px-2 text-sm"
      />
      <button type="submit" disabled={disabled || !text} className="rounded-md px-2 py-1 text-xs disabled:opacity-50">
        Save
      </button>
    </form>
  );
}

/** Only the private web operation may open the server machine's file manager, after an explicit click. */
function PathReveal({ settingKey, value, saving }: { settingKey: string; value: unknown; saving: boolean }) {
  const config = useConfig();
  const reveal = useMutation({
    mutationFn: () =>
      unwrapResponse(api.POST("/system/config/reveal-setting", { params: { query: { key: settingKey } } })),
  });
  if (!config.data?.can_reveal || typeof value !== "string" || !value) return null;
  return (
    <span>
      <button
        type="button"
        disabled={saving || reveal.isPending}
        onClick={() => reveal.mutate()}
        aria-label={`Show in ${config.data.file_manager}: ${settingKey}`}
        className="rounded-md px-2 py-1 text-xs hover:bg-accent disabled:opacity-50"
      >
        Show in {config.data.file_manager}
      </button>
      {(reveal.data || reveal.error) && (
        <span role={reveal.error ? "alert" : "status"} className="block text-xs text-muted-foreground">
          {reveal.error?.message ?? reveal.data?.message}
        </span>
      )}
    </span>
  );
}
