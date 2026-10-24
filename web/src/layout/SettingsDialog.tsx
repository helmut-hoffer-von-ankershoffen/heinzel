// Settings as a dialog over the screen, as in Claude: search and sections on the left, one row per setting on the
// right with its control. Every change is `hz system config set|unset` in the user's configuration.
import {
  Bot,
  Building2,
  ChevronDown,
  Cpu,
  FileCog,
  type LucideIcon,
  Monitor,
  Moon,
  Plug,
  Puzzle,
  Search,
  Settings2,
  Sun,
  Users,
  Workflow,
  X,
} from "lucide-react";
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import type { SchemaConfigReport, SchemaSettingInfo } from "@/api/client";
import { useConfig, useExpert, useSetExpert, useSetSetting, useSetTheme, useThemes } from "@/api/queries";
import { Loaded } from "@/components/common";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { NotificationSettings } from "@/notifications/NotificationSettings";
import { PluginsSection } from "./PluginsSection";
import { type Mode, useMode } from "./theme";

const FILES = "files";
const PLUGINS = "plugins";
const icons: Record<string, LucideIcon> = {
  hz: Settings2,
  system: Cpu,
  org: Building2,
  team: Users,
  workflow: Workflow,
  capabilities: Plug,
  runner: Bot,
};

/** `system.runner` → `system`: the left side lists modules; their sub-sections are headings on the right. */
const group = (section: string) => section.split(".")[0] ?? section;
const title = (name: string) =>
  name === "hz" ? t("settings.general") : (name.charAt(0).toUpperCase() + name.slice(1)).replaceAll("_", " ");
const label = (key: string) => (key.charAt(0).toUpperCase() + key.slice(1)).replaceAll("_", " ");

type Props = {
  section: string | null;
  initialQuery: string;
  onSection: (section: string) => void;
  onClose: () => void;
};

export function SettingsDialog({ section, initialQuery, onSection, onClose }: Props) {
  const config = useConfig();
  const [query, setQuery] = useState(initialQuery);
  const searchRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  useEffect(() => searchRef.current?.focus(), []);
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") onClose();
  };
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: a click on the backdrop closes it; Escape and the close button do for keyboards
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex h-[min(52rem,calc(100vh-2rem))] w-[min(64rem,100%)] flex-col overflow-hidden rounded-xl border border-border bg-popover shadow-2xl"
      >
        <header className="flex h-12 shrink-0 items-center border-b border-border pr-2 pl-5">
          <h2 id={titleId} className="text-ui font-medium">
            {t("settings.title")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("settings.close")}
            className="ml-auto flex size-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <X aria-hidden className="size-(--icon)" />
          </button>
        </header>
        <Loaded query={config}>
          {(report) => (
            <Body
              report={report}
              query={query}
              setQuery={setQuery}
              searchRef={searchRef}
              section={section}
              onSection={onSection}
            />
          )}
        </Loaded>
      </div>
    </div>
  );
}

type BodyProps = {
  report: SchemaConfigReport;
  query: string;
  setQuery: (query: string) => void;
  searchRef: React.RefObject<HTMLInputElement | null>;
  section: string | null;
  onSection: (section: string) => void;
};

function Body({ report, query, setQuery, searchRef, section, onSection }: BodyProps) {
  const groups = [...new Set(report.settings.map((setting) => group(setting.section)))].sort((a, b) =>
    a === "hz" ? -1 : b === "hz" ? 1 : a.localeCompare(b),
  );
  const current =
    section && (groups.includes(section) || [FILES, PLUGINS].includes(section)) ? section : (groups[0] ?? FILES);
  const needle = query.trim().toLowerCase();
  const matching = needle
    ? report.settings.filter((s) => `${s.section}.${s.key} ${s.description}`.toLowerCase().includes(needle))
    : report.settings.filter((s) => group(s.section) === current);
  const item = (active: boolean) =>
    cn(
      "flex w-full items-center gap-3 rounded-lg px-2.5 py-1.5 text-left text-ui text-foreground/90 transition-colors hover:bg-accent/70 [&_svg]:size-(--icon) [&_svg]:shrink-0 [&_svg]:stroke-[1.75] [&_svg]:text-icon",
      active && !needle && "bg-accent text-foreground",
    );
  return (
    <div className="flex min-h-0 flex-1">
      <nav
        aria-label={t("settings.title")}
        className="flex w-60 shrink-0 flex-col gap-0.5 overflow-y-auto p-3 max-sm:hidden"
      >
        <label className="mb-3 flex items-center gap-2 rounded-lg bg-input px-2.5 py-1.5 text-muted-foreground focus-within:ring-1 focus-within:ring-ring">
          <Search aria-hidden className="size-4 shrink-0" />
          <span className="sr-only">{t("settings.filter")}</span>
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("search.open")}
            className="w-full bg-transparent text-ui text-foreground outline-none placeholder:text-muted-foreground"
          />
        </label>
        <p className="mb-1 px-2.5 text-ui-sm text-muted-foreground">{t("settings.modules")}</p>
        {groups.map((name) => {
          const Icon = icons[name] ?? Puzzle;
          return (
            <button
              key={name}
              type="button"
              onClick={() => {
                setQuery("");
                onSection(name);
              }}
              aria-current={current === name && !needle ? "page" : undefined}
              className={item(current === name)}
            >
              <Icon aria-hidden />
              {title(name)}
            </button>
          );
        })}
        <p className="mt-4 mb-1 px-2.5 text-ui-sm text-muted-foreground">{t("settings.customize")}</p>
        <button
          type="button"
          onClick={() => {
            setQuery("");
            onSection(PLUGINS);
          }}
          className={item(current === PLUGINS)}
        >
          <Puzzle aria-hidden />
          {t("plugins.title")}
        </button>
        <p className="mt-4 mb-1 px-2.5 text-ui-sm text-muted-foreground">{t("settings.thisComputer")}</p>
        <button
          type="button"
          onClick={() => {
            setQuery("");
            onSection(FILES);
          }}
          className={item(current === FILES)}
        >
          <FileCog aria-hidden />
          {t("settings.filesTitle")}
        </button>
      </nav>
      <div className="min-w-0 flex-1 overflow-y-auto border-l border-border px-6 pt-5 pb-10">
        {current === PLUGINS && !needle ? (
          <PluginsSection />
        ) : current === FILES && !needle ? (
          <Files report={report} />
        ) : (
          <>
            {current === "hz" && !needle && (
              <>
                <h3 className="mb-1 text-ui font-medium">{t("settings.appearance")}</h3>
                <ModeRow />
                <ThemeRow />
                <div className="mt-6">
                  <NotificationSettings />
                </div>
                <div className="mt-6">
                  <ExpertRow />
                </div>
              </>
            )}
            <Sections settings={matching} heading={needle ? t("settings.results") : title(current)} />
          </>
        )}
      </div>
    </div>
  );
}

function Sections({ settings, heading }: { settings: SchemaSettingInfo[]; heading: string }) {
  const sections = [...new Set(settings.map((setting) => setting.section))];
  return (
    <>
      <h3 className="mb-2 text-ui font-medium">{heading}</h3>
      {settings.length === 0 && <p className="text-sm text-muted-foreground">{t("settings.none")}</p>}
      {sections.map((section) => (
        <section key={section} aria-label={section} className="mt-4">
          {sections.length > 1 || section.includes(".") ? (
            <h4 className="mb-1 font-mono text-ui-sm text-muted-foreground">[{section}]</h4>
          ) : null}
          <ul>
            {settings
              .filter((setting) => setting.section === section)
              .map((setting) => (
                <Row key={setting.key} setting={setting} />
              ))}
          </ul>
        </section>
      ))}
    </>
  );
}

/** One compact settings row: label (and a line of help) on the left, its control on the right. */
function SettingRow({ label, help, children }: { label: string; help?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-6 border-b border-border/70 py-3">
      <div className="min-w-0 flex-1">
        <p className="text-ui">{label}</p>
        {help && <p className="mt-0.5 text-sm text-muted-foreground">{help}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

/** System, light or dark, as three icons. */
function ModeRow() {
  const [mode, setMode] = useMode();
  const options: [Mode, typeof Sun, string][] = [
    ["system", Monitor, t("settings.system")],
    ["light", Sun, t("settings.light")],
    ["dark", Moon, t("settings.dark")],
  ];
  return (
    <SettingRow label={t("settings.mode")}>
      <div role="radiogroup" aria-label={t("settings.mode")} className="flex gap-0.5 rounded-lg bg-muted p-0.5">
        {options.map(([value, Icon, label]) => (
          // biome-ignore lint/a11y/useSemanticElements: a segmented control, announced as the radios of its group
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={mode === value}
            aria-label={label}
            title={label}
            onClick={() => setMode(value)}
            className={cn(
              "flex size-7 items-center justify-center rounded-md transition-colors",
              mode === value ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon aria-hidden className="size-4" />
          </button>
        ))}
      </div>
    </SettingRow>
  );
}

/** `hz system theme use`: the default look or one a plugin brings, such as your company's. */
function ThemeRow() {
  const themes = useThemes();
  const change = useSetTheme();
  const id = useId();
  const list = themes.data?.themes ?? [];
  return (
    <SettingRow label={t("settings.theme")} help={change.error?.message ?? themes.data?.note ?? undefined}>
      <label htmlFor={id} className="sr-only">
        {t("settings.theme")}
      </label>
      <span className="relative flex items-center">
        <select
          id={id}
          value={themes.data?.selected ?? "default"}
          disabled={!themes.data || change.isPending}
          onChange={(event) => change.mutate(event.target.value)}
          className="h-8 appearance-none rounded-lg bg-transparent py-0 pr-7 pl-2 text-right text-sm text-foreground hover:bg-accent/60 disabled:opacity-50"
        >
          {list.map((theme) => (
            <option key={theme.name} value={theme.name}>
              {theme.plugin ? `${theme.title} · ${theme.plugin.replace(/^heinzel-/, "")}` : theme.title}
            </option>
          ))}
        </select>
        <ChevronDown aria-hidden className="pointer-events-none absolute right-2 size-4 text-muted-foreground" />
      </span>
    </SettingRow>
  );
}

/** `hz system expert on|off` (ADR 0041): which screens and commands are listed; every one still works. */
function ExpertRow() {
  const expert = useExpert();
  const change = useSetExpert();
  const labelId = useId();
  const on = expert.data?.expert ?? false;
  return (
    <div className="mb-2 flex items-start gap-6 border-b border-border/70 pb-4">
      <div className="min-w-0 flex-1">
        <p id={labelId} className="text-ui">
          {t("settings.expert")}
        </p>
        <p className="mt-0.5 text-sm text-muted-foreground">{on ? t("settings.expertOn") : t("settings.expertOff")}</p>
        {expert.data?.note && (
          <p role="alert" className="mt-1 text-xs text-warn">
            {expert.data.note}
          </p>
        )}
        {change.error && (
          <p role="alert" className="mt-1 text-xs text-bad">
            {t("settings.expertFailed", { problem: change.error.message })}
          </p>
        )}
      </div>
      <Switch
        labelledBy={labelId}
        on={on}
        disabled={!expert.data || change.isPending}
        onToggle={() => change.mutate(!on)}
      />
    </div>
  );
}

type SwitchProps = { id?: string; labelledBy?: string; on: boolean; disabled: boolean; onToggle: () => void };

function Switch({ id, labelledBy, on, disabled, onToggle }: SwitchProps) {
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
        on ? "bg-[#2f7bf6]" : "bg-accent",
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

function Row({ setting }: { setting: SchemaSettingInfo }) {
  const save = useSetSetting();
  const key = `${setting.section}.${setting.key}`;
  const fromEnv = setting.source === "env";
  const ownValue = setting.source === "user" || setting.source === "project";
  const value = setting.value ?? setting.default;
  const inputId = useId();
  return (
    <li className="flex items-start gap-6 border-b border-border/70 py-4 last:border-b-0">
      <div className="min-w-0 flex-1">
        <label htmlFor={inputId} className="text-ui">
          {label(setting.key)}
        </label>
        <p className="mt-0.5 text-sm text-muted-foreground">{setting.description}</p>
        {fromEnv && <p className="mt-1 text-xs text-warn">{t("settings.fromEnv", { env: setting.env })}</p>}
        {save.error && (
          <p role="alert" className="mt-1 text-xs text-bad">
            {save.error.message}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2 pt-0.5">
        {ownValue && !fromEnv && (
          <button
            type="button"
            onClick={() => save.mutate({ key, value: null })}
            className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            {t("settings.reset")}
          </button>
        )}
        {typeof value === "boolean" ? (
          <Switch
            id={inputId}
            on={value}
            disabled={fromEnv || save.isPending}
            onToggle={() => save.mutate({ key, value: String(!value) })}
          />
        ) : (
          <TextValue
            id={inputId}
            value={setting.value === null || setting.value === undefined ? "" : asConfig(setting.value)}
            placeholder={
              setting.default === null || setting.default === undefined
                ? t("settings.notSet")
                : asConfig(setting.default)
            }
            numeric={typeof value === "number"}
            disabled={fromEnv || save.isPending}
            onCommit={(text) => save.mutate({ key, value: text === "" ? null : text })}
          />
        )}
      </div>
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

function Files({ report }: { report: SchemaConfigReport }) {
  return (
    <>
      <h3 className="mb-1 text-ui font-medium">{t("settings.filesTitle")}</h3>
      <p className="mb-4 text-sm text-muted-foreground">{t("settings.files")}</p>
      <ul>
        {report.files.map((file) => (
          <li key={file.path} className="flex items-center gap-4 border-b border-border/70 py-3 last:border-b-0">
            <div className="min-w-0 flex-1">
              <p className="text-ui capitalize">{file.scope}</p>
              <code className="font-mono text-xs break-all text-muted-foreground">{file.path}</code>
            </div>
            <span className="text-xs text-muted-foreground">
              {file.exists ? t("settings.exists") : t("settings.missing")}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}
