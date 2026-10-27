// Settings as a dialog over the screen, as in Claude: search and sections on the left, one row per setting on the
// right with its control. Every change is `hz system config set|unset` in the user's configuration.

import { FolderOpen, Monitor, Moon, Sun, X } from "lucide-react";
import { type KeyboardEvent, type ReactNode, useId, useRef, useState } from "react";
import type { SchemaConfigReport, SchemaSettingInfo } from "@/api/client";
import { useConfig, useExpert, useInfo, useNavigation, usePlugins, useRevealConfig, useSetExpert } from "@/api/queries";
import { ContributionControls, ContributionToggles } from "@/components/Contributions";
import { Loaded } from "@/components/common";
import { Button } from "@/components/ui/button";
import { SearchField } from "@/components/ui/search-field";
import { type MessageKey, t } from "@/i18n";
import { cn } from "@/lib/utils";
import { NotificationSettings } from "@/notifications/NotificationSettings";
import { SoundSettings } from "@/sound/SoundControls";
import { AiAccessSetting } from "./AiAccessSetting";
import { ConnectorsSection } from "./ConnectorsSection";
import { DaemonSettings } from "./DaemonSettings";
import { FROM_CLAUDE, FromClaudeSection } from "./FromClaudeSection";
import { ModuleHeader, PluginsSection } from "./PluginsSection";
import { Row, Switch } from "./SettingControls";
import type { JsonSchema } from "./settingSchema";
import {
  CONNECTORS,
  FILES,
  group,
  type Heading,
  inWords,
  type Page,
  PLUGINS,
  pageOf,
  plainSummary,
  resolvePage,
  settingsNav,
} from "./settingsSections";
import { ThemePreviews } from "./ThemePreviews";
import { type Mode, useMode } from "./theme";

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
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") onClose();
  };
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: a click on the backdrop closes it; Escape and the close button do for keyboards
    <div
      className="viewport-overlay fixed z-(--layer-modal) flex items-center justify-center bg-black/50 p-4"
      onMouseDown={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex h-[min(52rem,100%)] w-[min(64rem,100%)] flex-col overflow-hidden rounded-xl border border-border bg-popover shadow-2xl"
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
  const navigation = useNavigation();
  const info = useInfo();
  const expert = useExpert().data?.expert === true;
  // Deprecated settings are never shown: hz migrates them, and doctor names the fix when one is still set.
  const settings = report.settings.filter((setting) => {
    const schema = setting.json_schema as JsonSchema | null | undefined;
    return !schema?.deprecated && !schema?.["x-hidden"] && (expert || !schema?.["x-expert"]);
  });
  const groups = [...new Set(settings.map((setting) => group(setting.section)))];
  const nav = settingsNav(groups, plugins.data ?? [], navigation.data ?? []);
  const connector = nav.connectors.find((entry) => entry.id === section || entry.groups.includes(section ?? ""));
  // A connector's own page is part of Connectors; a section nobody knows (yet: the plugin list may still load) is the
  // first page's.
  const current = connector ? CONNECTORS : (resolvePage(section, nav.pages) ?? "appearance");
  const page = nav.pages.find((entry) => entry.id === current) as Page;
  const needle = query.trim().toLowerCase();
  const text = (s: SchemaSettingInfo) =>
    `${s.section}.${s.key} ${s.section_title ?? ""} ${(s.json_schema as JsonSchema | null | undefined)?.title ?? ""} ${s.description}`.toLowerCase();
  const matching = needle
    ? settings.filter((s) => text(s).includes(needle))
    : settings.filter((s) => pageOf(s.section, nav.pages)?.id === current);
  // Pages found by their name or what is on them (the mode, the AI access, the sound), which aren't schema settings.
  const pageHits = needle
    ? nav.pages.filter((entry) => `${entry.title} ${entry.keywords ?? ""}`.toLowerCase().includes(needle))
    : [];
  const item = (active: boolean) =>
    cn(
      "flex w-full items-center gap-3 rounded-lg px-2.5 py-1.5 text-left text-ui text-foreground/90 transition-colors hover:bg-accent/70 [&_svg]:size-(--icon) [&_svg]:shrink-0 [&_svg]:stroke-[1.75] [&_svg]:text-icon",
      active && !needle && "bg-accent text-accent-foreground [&_svg]:text-accent-foreground",
    );
  const go = (id: string) => {
    setQuery("");
    onSection(id);
  };
  const entry = (entry: Page) => (
    <button
      key={entry.id}
      type="button"
      onClick={() => go(entry.id)}
      aria-current={current === entry.id && !needle ? "page" : undefined}
      className={cn(item(current === entry.id), entry.module === "clock" && "mt-2")}
    >
      <entry.icon aria-hidden />
      {entry.title}
    </button>
  );
  const heading = (id: Heading, label: MessageKey, first = false) => (
    <>
      <p className={cn("mb-1 px-2.5 text-ui-sm text-muted-foreground", !first && "mt-4")}>{t(label)}</p>
      {nav.pages.filter((entry) => entry.heading === id).map(entry)}
    </>
  );
  const header = (
    <ModuleHeader
      title={page.title}
      plugin={page.plugin}
      provider={page.provider}
      version={info.data?.version}
      onOpen={() => go(PLUGINS)}
    />
  );
  return (
    <div className="flex min-h-0 flex-1 max-sm:flex-col">
      <nav
        aria-label={t("settings.title")}
        className="flex w-60 shrink-0 flex-col gap-0.5 overflow-y-auto p-3 max-sm:w-full max-sm:overflow-visible max-sm:border-b max-sm:border-border"
      >
        <label className="mb-2 flex flex-col gap-1 text-sm sm:hidden">
          <span>{t("settings.page")}</span>
          <select
            aria-label={t("settings.page")}
            value={current}
            onChange={(event) => go(event.target.value)}
            className="h-9 w-full rounded-lg border border-border bg-input px-2 pr-8 text-ui"
          >
            {(["preferences", "customize", "organisation", "computer"] as const).flatMap((heading) =>
              nav.pages
                .filter((entry) => entry.heading === heading)
                .map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.title}
                  </option>
                )),
            )}
          </select>
        </label>
        <SearchField
          ref={searchRef}
          label={t("settings.filter")}
          quietFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("search.open")}
          className="mb-3"
        />
        <div className="max-sm:hidden">
          {heading("preferences", "settings.preferences", true)}
          {heading("customize", "settings.customize")}
          {heading("organisation", "settings.organisation")}
          {heading("computer", "settings.thisComputer")}
        </div>
      </nav>
      <div
        data-settings-content
        className="min-w-0 flex-1 overflow-y-auto [overflow-wrap:anywhere] border-l border-border px-6 pt-5 pb-10 max-sm:border-l-0 max-sm:px-4"
      >
        {needle ? (
          <>
            {pageHits.length > 0 && (
              <section aria-label={t("settings.pages")} className="mb-4">
                <h3 className="mb-1 text-ui font-medium">{t("settings.pages")}</h3>
                <ul className="flex flex-wrap gap-2">
                  {pageHits.map((hit) => (
                    <li key={hit.id}>
                      <Button variant="outline" size="sm" onClick={() => go(hit.id)}>
                        <hit.icon aria-hidden />
                        {hit.title}
                      </Button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {(matching.length > 0 || pageHits.length === 0) && (
              <Sections settings={matching} heading={t("settings.results")} />
            )}
          </>
        ) : current === PLUGINS ? (
          <PluginsSection />
        ) : current === FILES ? (
          <>
            <Files report={report} />
            <div className="mt-8">
              <Sections settings={matching} heading={t("settings.folders")} />
            </div>
          </>
        ) : current === FROM_CLAUDE ? (
          <FromClaudeSection />
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
            {current === "appearance" && (
              <>
                <ModuleHeader title={page.title} plugin={undefined} version={info.data?.version} onOpen={() => {}} />
                <ModeRow />
                <ThemePreviews />
                <div className="mt-6">
                  <SoundSettings />
                </div>
                <div className="mt-6">
                  <ExpertRow />
                </div>
              </>
            )}
            {current === "ai" && <AiAccessSetting />}
            {current === "notifications" && <NotificationSettings />}
            {current === "daemon" && (
              <>
                {header}
                <DaemonSettings />
              </>
            )}
            {(matching.length > 0 || !["appearance", "ai", "notifications", "daemon"].includes(current)) && (
              <Sections
                settings={matching}
                heading={current === "daemon" ? t("daemon.configuration") : page.title}
                header={["appearance", "ai", "notifications", "daemon"].includes(current) ? undefined : header}
                summary={page.plugin?.summary ? plainSummary(page.plugin.summary) : undefined}
              />
            )}
            {(page.module || TOGGLES[current]) && (
              <>
                <ContributionControls module={page.module ?? TOGGLES[current] ?? ""} />
                <ContributionToggles module={page.module ?? TOGGLES[current] ?? ""} />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** The module whose contributions a page can show in the app (`hz system ui show|hide`). */
const TOGGLES: Record<string, string> = { you: "org", daemon: "system" };

function Sections({
  settings,
  heading,
  summary,
  header,
}: {
  settings: SchemaSettingInfo[];
  heading: string;
  summary?: string;
  /** The module's header line (name, kinds, provenance); without it, the heading alone. */
  header?: ReactNode;
}) {
  const sections = [...new Set(settings.map((setting) => setting.section))];
  return (
    <>
      {header ?? <h3 className="mb-2 text-ui font-medium">{heading}</h3>}
      {summary && <p className="-mt-1 mb-2 text-sm text-muted-foreground">{summary}</p>}
      {settings.length === 0 && <p className="text-sm text-muted-foreground">{t("settings.none")}</p>}
      {sections.map((section) => {
        const sectionTitle = settings.find((setting) => setting.section === section)?.section_title;
        // Some modules use their TOML section path (for example `[system.runner]`) as a placeholder title.
        // It is configuration syntax, not useful UI copy; derive a human-readable heading instead.
        const isRawMarker = sectionTitle === `[${section}]`;
        const title = !sectionTitle || isRawMarker ? inWords(section.split(".").at(-1) ?? section) : sectionTitle;
        return (
          <section key={section} aria-label={title} className="mt-4">
            {(sections.length > 1 || title !== heading) && (
              <h4 className="mb-1 text-ui-sm text-muted-foreground">{title}</h4>
            )}
            <ul>
              {settings
                .filter((setting) => setting.section === section)
                .map((setting) => (
                  <Row key={setting.key} setting={setting} />
                ))}
            </ul>
          </section>
        );
      })}
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
