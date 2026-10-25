// Settings as a dialog over the screen, as in Claude: search and sections on the left, one row per setting on the
// right with its control. Every change is `hz system config set|unset` in the user's configuration.
import {
  Bot,
  Building2,
  Cpu,
  FileCog,
  FolderOpen,
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
import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";
import type { SchemaConfigReport, SchemaSettingInfo } from "@/api/client";
import { useConfig, useExpert, useInfo, usePlugins, useRevealConfig, useSetExpert } from "@/api/queries";
import { Loaded } from "@/components/common";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { NotificationSettings } from "@/notifications/NotificationSettings";
import { ConnectorsSection } from "./ConnectorsSection";
import { PluginsSection, Provenance } from "./PluginsSection";
import { Row, Switch } from "./SettingControls";
import type { JsonSchema } from "./settingSchema";
import {
  CONNECTORS,
  displayName,
  FILES,
  group,
  PLUGINS,
  plainSummary,
  type SettingsEntry,
  settingsNav,
} from "./settingsSections";
import { ThemePreviews } from "./ThemePreviews";
import { type Mode, useMode } from "./theme";

const icons: Record<string, LucideIcon> = {
  hz: Settings2,
  system: Cpu,
  org: Building2,
  team: Users,
  heinzel: Workflow,
  capabilities: Plug,
  runner: Bot,
};

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
  const plugins = usePlugins();
  const info = useInfo();
  // Deprecated settings are never shown: hz migrates them, and doctor names the fix when one is still set.
  const settings = report.settings.filter(
    (setting) => !(setting.json_schema as JsonSchema | null | undefined)?.deprecated,
  );
  const groups = [...new Set(settings.map((setting) => group(setting.section)))];
  const nav = settingsNav(groups, plugins.data ?? []);
  const connector = nav.connectors.find((entry) => entry.id === section || entry.groups.includes(section ?? ""));
  const preference = nav.preferences.find((entry) => entry.id === section);
  // A connector's own page is part of Connectors; a section nobody knows (yet: the plugin list may still load) is
  // General's.
  const current = connector
    ? CONNECTORS
    : preference || [FILES, PLUGINS, CONNECTORS].includes(section ?? "")
      ? (section as string)
      : (nav.preferences[0]?.id ?? FILES);
  const needle = query.trim().toLowerCase();
  const titled = (entry: SettingsEntry) => entry.title || t("settings.general");
  const matching = needle
    ? settings.filter((s) => `${s.section}.${s.key} ${s.description}`.toLowerCase().includes(needle))
    : settings.filter((s) => group(s.section) === current);
  const item = (active: boolean) =>
    cn(
      "flex w-full items-center gap-3 rounded-lg px-2.5 py-1.5 text-left text-ui text-foreground/90 transition-colors hover:bg-accent/70 [&_svg]:size-(--icon) [&_svg]:shrink-0 [&_svg]:stroke-[1.75] [&_svg]:text-icon",
      active && !needle && "bg-accent text-accent-foreground [&_svg]:text-accent-foreground",
    );
  const go = (id: string) => {
    setQuery("");
    onSection(id);
  };
  const entry = (id: string, label: string, Icon: LucideIcon) => (
    <button
      key={id}
      type="button"
      onClick={() => go(id)}
      aria-current={current === id && !needle ? "page" : undefined}
      className={item(current === id)}
    >
      <Icon aria-hidden />
      {label}
    </button>
  );
  return (
    <div className="flex min-h-0 flex-1">
      <nav
        aria-label={t("settings.title")}
        className="flex w-60 shrink-0 flex-col gap-0.5 overflow-y-auto p-3 max-sm:hidden"
      >
        <label className="mb-3 flex items-center gap-2 rounded-lg bg-input px-2.5 py-1.5 text-icon focus-within:ring-1 focus-within:ring-ring">
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
        <p className="mb-1 px-2.5 text-ui-sm text-muted-foreground">{t("settings.preferences")}</p>
        {nav.preferences.map((preference) => entry(preference.id, titled(preference), icons[preference.id] ?? Puzzle))}
        <p className="mt-4 mb-1 px-2.5 text-ui-sm text-muted-foreground">{t("settings.customize")}</p>
        {entry(CONNECTORS, t("connectors.title"), Plug)}
        {entry(PLUGINS, t("plugins.title"), Puzzle)}
        <p className="mt-4 mb-1 px-2.5 text-ui-sm text-muted-foreground">{t("settings.thisComputer")}</p>
        {entry(FILES, t("settings.filesTitle"), FileCog)}
      </nav>
      <div className="min-w-0 flex-1 overflow-y-auto border-l border-border px-6 pt-5 pb-10">
        {needle ? (
          <Sections settings={matching} heading={t("settings.results")} />
        ) : current === PLUGINS ? (
          <PluginsSection />
        ) : current === FILES ? (
          <Files report={report} />
        ) : current === CONNECTORS ? (
          <ConnectorsSection
            connectors={nav.connectors}
            open={connector}
            settings={settings}
            onOpen={go}
            renderSettings={(settings, heading) => <Sections settings={settings} heading={heading} />}
          />
        ) : (
          <>
            {current === "hz" && (
              <>
                <h3 className="mb-1 text-ui font-medium">{t("settings.appearance")}</h3>
                <ModeRow />
                <ThemePreviews />
                <div className="mt-6">
                  <NotificationSettings />
                </div>
                <div className="mt-6">
                  <ExpertRow />
                </div>
              </>
            )}
            <Sections
              settings={matching}
              heading={preference ? titled(preference) : displayName(current)}
              summary={preference?.plugin?.summary ? plainSummary(preference.plugin.summary) : undefined}
              provenance={
                <Provenance plugin={preference?.plugin} version={info.data?.version} onOpen={() => go(PLUGINS)} />
              }
            />
          </>
        )}
      </div>
    </div>
  );
}

function Sections({
  settings,
  heading,
  summary,
  provenance,
}: {
  settings: SchemaSettingInfo[];
  heading: string;
  summary?: string;
  provenance?: ReactNode;
}) {
  const sections = [...new Set(settings.map((setting) => setting.section))];
  return (
    <>
      <h3 className="mb-2 text-ui font-medium">{heading}</h3>
      {provenance}
      {summary && <p className="-mt-1 mb-2 text-sm text-muted-foreground">{summary}</p>}
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

function Files({ report }: { report: SchemaConfigReport }) {
  const reveal = useRevealConfig();
  // `open -R` selects the file in Finder; elsewhere the folder opens in the file manager.
  const label = report.file_manager === "Finder" ? t("settings.showInFinder") : t("settings.openFolder");
  const revealable = (scope: string): scope is "site" | "user" | "project" =>
    scope === "site" || scope === "user" || scope === "project";
  return (
    <>
      <h3 className="mb-1 text-ui font-medium">{t("settings.filesTitle")}</h3>
      <p className="mb-4 text-sm text-muted-foreground">{t("settings.files")}</p>
      <ul>
        {report.files.map((file) => {
          const scope = file.scope;
          return (
            <li key={file.path} className="flex items-center gap-4 border-b border-border/70 py-3 last:border-b-0">
              <div className="min-w-0 flex-1">
                <p className="text-ui capitalize">{scope}</p>
                <code className="font-mono text-xs break-all text-muted-foreground">{file.path}</code>
              </div>
              <span className="text-xs text-muted-foreground">
                {file.exists ? t("settings.exists") : t("settings.missing")}
              </span>
              {report.can_reveal && revealable(scope) && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={reveal.isPending}
                  onClick={() => reveal.mutate(scope)}
                  aria-label={`${label}: ${scope}`}
                >
                  <FolderOpen aria-hidden />
                  {label}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
      {(reveal.error || (reveal.data && !reveal.data.opened)) && (
        <p role="alert" className="mt-2 text-xs text-warn">
          {reveal.error?.message ?? reveal.data?.message}
        </p>
      )}
    </>
  );
}
