// Settings → Plugins, as in a desktop chat app: "Yours" lists what's installed, "Discover" what can be (first-party
// and community plugins), "Add" installs from a name or a git URL. Installing runs code, so it takes two steps: the
// backend first answers what a spec would install (source, version, publisher, pinned), and only the confirm sends
// that pinned requirement with `confirm=true` (ADR 0047). The app then restarts `hz ui` itself and reloads.
import {
  BookOpen,
  ExternalLink,
  type LucideIcon,
  Package,
  Palette,
  Plug,
  Plus,
  Power,
  PowerOff,
  ShieldCheck,
  Sparkles,
  Trash2,
  Users,
} from "lucide-react";
import { useRef, useState } from "react";
import type { SchemaPluginCandidate, SchemaPluginInfo } from "@/api/client";
import {
  type PluginChangeRequest,
  useChangePlugin,
  usePluginPreview,
  usePluginSearch,
  usePlugins,
} from "@/api/queries";
import { Loaded } from "@/components/common";
import { Button } from "@/components/ui/button";
import { SearchField } from "@/components/ui/search-field";
import { type MessageKey, t } from "@/i18n";
import { cn } from "@/lib/utils";
import { RestartAndApply } from "@/lifecycle/RestartAndApply";
import { displayName } from "./settingsSections";

type PluginKind = NonNullable<SchemaPluginInfo["kinds"]>[number];

/** The catalog may identify a community/private source; absence of that evidence means only "Plugin". */
export function sourceLabel(plugin: SchemaPluginInfo, community?: boolean): string {
  if (plugin.first_party) return t("plugins.firstParty");
  return community === undefined ? t("plugins.plugin") : community ? t("plugins.community") : t("plugins.private");
}

/** The head of a module's page, the same everywhere: its name with the kinds of what it adds on the left, and on
 *  the right, on the same line, where it comes from (First-party, Contrib, Private) and its version. */
export function ModuleHeader({
  title,
  plugin,
  version,
  onOpen,
  provider,
}: {
  title: string;
  plugin: SchemaPluginInfo | undefined;
  version: string | undefined;
  onOpen: () => void;
  provider?: string | null;
}) {
  return (
    <div className="mb-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h3 className="text-ui font-medium">{title}</h3>
        {plugin && <KindBadges plugin={plugin} />}
        <p className="ml-auto flex items-center gap-2 text-ui-sm text-muted-foreground">
          {plugin ? (
            <>
              <button
                type="button"
                onClick={onOpen}
                className="rounded-md border border-border px-1.5 text-[11px] hover:bg-accent/60"
                title={t("settings.openPlugin")}
              >
                {sourceLabel(plugin)}
              </button>
              <span className="text-xs tabular-nums">v{plugin.version}</span>
            </>
          ) : provider ? (
            <span title={provider}>{t("plugins.plugin")}</span>
          ) : (
            <>
              <a
                href={CORE}
                target="_blank"
                rel="noreferrer noopener"
                className="rounded-md border border-border px-1.5 text-[11px] hover:bg-accent/60"
              >
                {t("plugins.firstParty")}
              </a>
              {version && <span className="text-xs tabular-nums">v{version}</span>}
            </>
          )}
        </p>
      </div>
      {plugin && <RepositoryLine url={plugin.source_url ?? plugin.homepage} />}
    </div>
  );
}

/** The browsable repository of an install spec: `name @ git+https://host/o/r@ref#subdirectory=d` → its tree. */
export function repositoryOf(spec: string): string | null {
  const match = spec.match(/git\+(https:\/\/[^@#\s]+?)(?:\.git)?(?:@([^#\s]+))?(?:#subdirectory=(\S+))?$/);
  if (!match) return null;
  const [, repo, ref, subdirectory] = match;
  const url = new URL(repo as string);
  url.username = "";
  url.password = "";
  const base = url.toString().replace(/\/$/, "");
  return subdirectory ? `${base}/tree/${ref ?? "main"}/${subdirectory}` : base;
}

const CORE = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel";

/** A catalog entry's repository: the backend's `repository_url`, else derived from the install spec. */
function repositoryOfEntry(entry: {
  name: string;
  source: string;
  install_spec: string;
  repository_url?: string | null;
}): string | null {
  if (entry.repository_url) return entry.repository_url;
  return entry.source === "first-party" ? `${CORE}/tree/main/plugins/${entry.name}` : repositoryOf(entry.install_spec);
}

/** `https://github.com/o/r/tree/main/plugins/x` → `github.com/o/r`: which repository it comes from. */
export function repositoryLabel(url: string): string {
  const { host, pathname } = new URL(url);
  return [host, ...pathname.split("/").filter(Boolean).slice(0, 2)].join("/");
}

/** Where a plugin's code lives, as a link button. */
function sourceLink(url: string | null | undefined, name: string) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      aria-label={t("plugins.source", { name })}
      title={url}
      className="flex size-8 shrink-0 items-center justify-center rounded-lg text-icon hover:bg-accent"
    >
      <ExternalLink aria-hidden className="size-4" />
    </a>
  );
}

/** "from github.com/owner/repo", the repository a link; "source unknown" until the backend says. */
function RepositoryLine({ url }: { url: string | null | undefined }) {
  return url ? (
    <span className="block truncate text-xs text-muted-foreground/80">
      {t("plugins.fromPrefix")}{" "}
      <a href={url} target="_blank" rel="noreferrer" className="hover:text-foreground hover:underline">
        {repositoryLabel(url)}
      </a>
    </span>
  ) : (
    <span className="block text-xs text-muted-foreground/60">{t("plugins.sourceUnknown")}</span>
  );
}

type Pending =
  | { action: "add"; candidate: SchemaPluginCandidate }
  | { action: "remove" | "enable" | "disable"; name: string };

function request(pending: Pending): PluginChangeRequest {
  switch (pending.action) {
    case "add":
      // A first-party plugin is added by the name asked for; its git requirement is refused as a spec.
      return { add: pending.candidate.first_party ? pending.candidate.spec : pending.candidate.requirement };
    case "remove":
      return { remove: pending.name };
    case "enable":
      return { enable: pending.name };
    case "disable":
      return { disable: pending.name };
  }
}

/** What a confirmed `add` installs: the backend's answer, shown before anything runs. */
function CandidateDetails({ candidate }: { candidate: SchemaPluginCandidate }) {
  const rows: [string, string][] = [
    [t("plugins.field.plugin"), candidate.distribution ?? t("plugins.unknown")],
    [t("plugins.field.version"), candidate.version ?? t("plugins.unknown")],
    [t("plugins.field.publisher"), candidate.publisher ?? t("plugins.unknown")],
    [
      t("plugins.field.source"),
      `${candidate.first_party ? `${t("plugins.firstParty")}, ` : ""}${candidate.source}${
        candidate.private ? `, ${t("plugins.privateRepository")}` : ""
      }`,
    ],
  ];
  if (candidate.installed_version) rows.push([t("plugins.field.replaces"), candidate.installed_version]);
  return (
    <div className="mt-2 text-xs">
      {candidate.summary && <p className="mb-1 text-muted-foreground">{candidate.summary}</p>}
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="break-all">{value}</dd>
          </div>
        ))}
      </dl>
      {candidate.url && <RepositoryLine url={candidate.url} />}
      <code className="mt-1 block font-mono break-all text-muted-foreground">{candidate.requirement}</code>
      {candidate.warnings.map((warning) => (
        <p key={warning} className="mt-1 text-warn">
          {warning}
        </p>
      ))}
    </div>
  );
}

export function PluginsSection({ reload, restartInterval }: { reload?: () => void; restartInterval?: number } = {}) {
  const [tab, setTab] = useState<"yours" | "discover">("yours");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [spec, setSpec] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [pluginProgress, setPluginProgress] = useState<string | null>(null);
  const pluginRestartRequired = useRef(false);
  const preview = usePluginPreview();
  const change = useChangePlugin();
  const tabClass = (active: boolean) =>
    cn(
      "rounded-lg px-3 py-1 text-sm transition-colors hover:bg-accent/70",
      active ? "bg-accent text-foreground" : "text-muted-foreground",
    );
  const show = (value: string) => {
    change.reset();
    setPluginProgress(null);
    preview.mutate(value, {
      onSuccess: (answer) => {
        if (answer.candidate) setPending({ action: "add", candidate: answer.candidate });
      },
    });
  };
  const ask = (value: Pending) => {
    change.reset();
    setPluginProgress(null);
    preview.reset();
    setPending(value);
  };
  const confirm = async () => {
    if (!pending) return false;
    setPluginProgress(null);
    const operation = request(pending);
    const answer = await change.mutateAsync(
      "add" in operation ? { ...operation, onProgress: setPluginProgress } : operation,
    );
    pluginRestartRequired.current = answer.restart_required;
    setPending(null);
    setAdding(false);
    setSpec("");
    return answer.restart_required;
  };
  const confirmText = () => {
    if (!pending) return "";
    switch (pending.action) {
      case "add":
        return t("plugins.confirmAdd");
      case "remove":
        return t("plugins.confirmRemove", { name: pending.name });
      case "enable":
        return t("plugins.confirmEnable", { name: pending.name });
      case "disable":
        return t("plugins.confirmDisable", { name: pending.name });
    }
  };
  const actionText = (action: Pending["action"]) =>
    ({
      add: t("plugins.install"),
      remove: t("plugins.remove"),
      enable: t("plugins.enable"),
      disable: t("plugins.disable"),
    })[action];
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h3 className="mr-2 text-ui font-medium">{t("plugins.title")}</h3>
        <div role="tablist" className="flex gap-1">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "yours"}
            className={tabClass(tab === "yours")}
            onClick={() => setTab("yours")}
          >
            {t("plugins.yours")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "discover"}
            className={tabClass(tab === "discover")}
            onClick={() => setTab("discover")}
          >
            {t("plugins.discover")}
          </button>
        </div>
        <SearchField
          label={t("plugins.search")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("search.open")}
          className="ml-auto py-1"
          inputClassName="w-36 text-sm"
        />
        <Button variant="outline" size="sm" onClick={() => setAdding((value) => !value)}>
          <Plus aria-hidden />
          {t("plugins.add")}
        </Button>
      </div>
      <p className="-mt-2 mb-4 text-sm text-muted-foreground">
        {tab === "yours" ? t("plugins.yoursIntro") : t("plugins.discoverIntro")}
      </p>
      {adding && (
        <form
          className="mb-4 flex flex-col gap-2 rounded-xl border border-border p-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (spec.trim()) show(spec.trim());
          }}
        >
          <label htmlFor="plugin-spec" className="text-sm">
            {t("plugins.addLabel")}
          </label>
          <div className="flex gap-2">
            <input
              id="plugin-spec"
              value={spec}
              onChange={(event) => setSpec(event.target.value)}
              placeholder="git+https://github.com/acme-example/heinzel-acme"
              className="h-8 min-w-0 flex-1 rounded-lg border border-border bg-input px-2.5 font-mono text-xs outline-none focus:border-muted-foreground/50"
            />
            <Button type="submit" size="sm" disabled={!spec.trim() || preview.isPending}>
              {preview.isPending ? t("plugins.checking") : t("plugins.install")}
            </Button>
          </div>
        </form>
      )}
      {preview.error && !pending && (
        <p role="alert" className="mb-4 text-sm text-bad">
          {preview.error.message}
        </p>
      )}
      {pending && (
        <fieldset className="mb-4 rounded-xl border border-warn/50 bg-warn/5 p-3 text-sm">
          <legend className="font-medium">{confirmText()}</legend>
          {pending.action === "add" && <CandidateDetails candidate={pending.candidate} />}
          <p className="mt-1 text-xs text-muted-foreground">{t("plugins.restartNote")}</p>
          {change.error && (
            <p role="alert" className="mt-1 text-xs text-bad">
              {change.error.message}
            </p>
          )}
          {change.isPending && pluginProgress && (
            <p role="status" className="mt-1 text-xs text-muted-foreground">
              {pluginProgress}
            </p>
          )}
          <div className="mt-2">
            <RestartAndApply
              target={t("plugins.title")}
              changes={[
                `${actionText(pending.action)}: ${pending.action === "add" ? (pending.candidate.distribution ?? pending.candidate.spec) : pending.name}`,
              ]}
              preserve={confirm}
              onApplied={() => {
                if (pluginRestartRequired.current) (reload ?? (() => window.location.reload()))();
              }}
              options={{
                ...(restartInterval ? { interval: restartInterval } : {}),
              }}
            />
            <Button size="sm" variant="ghost" onClick={() => setPending(null)} disabled={change.isPending}>
              {t("plugins.cancel")}
            </Button>
          </div>
        </fieldset>
      )}
      {tab === "yours" ? <Yours query={query} onChange={ask} /> : null}
      {tab === "discover" ? <Discover query={query} onInstall={show} /> : null}
    </>
  );
}

function Yours({ query, onChange }: { query: string; onChange: (pending: Pending) => void }) {
  const plugins = usePlugins();
  const catalog = usePluginSearch("");
  const [kind, setKind] = useState<PluginKind | null>(null);
  const known = new Map((catalog.data?.plugins ?? []).map((plugin) => [plugin.name, plugin]));
  return (
    <Loaded query={plugins}>
      {(list) => {
        const shown = list.filter(
          (plugin) =>
            plugin.distribution.toLowerCase().includes(query.toLowerCase()) &&
            (kind === null || (plugin.kinds ?? []).includes(kind)),
        );
        return (
          <>
            <KindFilter kind={kind} onKind={setKind} />
            {shown.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                {kind ? t("plugins.noneOfKind") : t("plugins.none")}
              </p>
            ) : (
              <ul>
                {shown.map((plugin) => {
                  const entry = known.get(plugin.distribution);
                  const source = sourceLabel(plugin, entry?.community);
                  const url = plugin.source_url ?? (entry ? repositoryOfEntry(entry) : null);
                  const enabled = plugin.enabled ?? true;
                  return (
                    <li
                      key={plugin.distribution}
                      className="flex items-center gap-3 border-b border-border/70 py-3 last:border-b-0"
                    >
                      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
                        <Package aria-hidden className="size-(--icon) text-icon" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2 text-ui">
                          {displayName(
                            plugin.name ?? plugin.distribution.replace(/^heinzel-/, ""),
                            plugin.summary || entry?.description,
                          )}
                          <KindBadges plugin={plugin} />
                        </span>
                        <span className="block truncate text-sm text-muted-foreground">
                          {plugin.error ??
                            (plugin.summary ||
                              entry?.description ||
                              t("plugins.modules", { modules: plugin.modules.join(", ") || "—" }))}
                        </span>
                        <RepositoryLine url={url} />
                      </span>
                      {/* Where it comes from and its version: their own column, flush right and aligned across rows. */}
                      <span className="flex shrink-0 items-center gap-2">
                        {!enabled && (
                          <span className="rounded-md border border-warn/50 px-1.5 text-[11px] text-muted-foreground">
                            {t("plugins.disabled")}
                          </span>
                        )}
                        <span className="rounded-md border border-border px-1.5 text-[11px] text-muted-foreground">
                          {source}
                        </span>
                        <span className={VERSION}>v{plugin.version}</span>
                      </span>
                      {sourceLink(url, plugin.distribution)}
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => onChange({ action: enabled ? "disable" : "enable", name: plugin.distribution })}
                        aria-label={t(enabled ? "plugins.disableName" : "plugins.enableName", {
                          name: plugin.distribution,
                        })}
                        title={enabled ? t("plugins.disable") : t("plugins.enable")}
                        className="text-icon"
                      >
                        {enabled ? <PowerOff aria-hidden /> : <Power aria-hidden />}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => onChange({ action: "remove", name: plugin.distribution })}
                        aria-label={t("plugins.removeName", { name: plugin.distribution })}
                        title={t("plugins.remove")}
                        className="text-icon"
                      >
                        <Trash2 aria-hidden />
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
          </>
        );
      }}
    </Loaded>
  );
}

/** The version's column: as wide in every row (Discover's rows keep it empty), so the badges before it line up. */
const VERSION = "w-14 shrink-0 text-right text-xs text-muted-foreground tabular-nums";

const KINDS: { kind: PluginKind; icon: LucideIcon; label: MessageKey }[] = [
  { kind: "connector", icon: Plug, label: "plugins.kind.connector" },
  { kind: "theme", icon: Palette, label: "plugins.kind.theme" },
  { kind: "capability", icon: Sparkles, label: "plugins.kind.capability" },
  { kind: "playbook", icon: BookOpen, label: "plugins.kind.playbook" },
];

/** What exactly a plugin brings of `kind`, in words: "Connector: tracker, documents". */
export function kindDetail(plugin: SchemaPluginInfo, kind: PluginKind): string {
  const names = (values: string[]) => [...new Set(values)].join(", ");
  const what =
    kind === "connector"
      ? names((plugin.capabilities ?? []).map((capability) => capability.split(":").at(-1) ?? capability))
      : kind === "theme"
        ? names(plugin.themes ?? [])
        : kind === "playbook"
          ? names(plugin.playbooks ?? [])
          : names(plugin.modules.map((module) => `hz ${module.replaceAll(".", " ")}`));
  const label = t(KINDS.find((entry) => entry.kind === kind)?.label ?? "plugins.kind.capability");
  return what ? `${label}: ${what}` : label;
}

/** A small badge per kind the plugin is, its tooltip saying what exactly. */
function KindBadges({ plugin }: { plugin: SchemaPluginInfo }) {
  const kinds = plugin.kinds ?? [];
  if (!kinds.length) return null;
  return (
    <span className="flex items-center gap-1">
      {KINDS.filter((entry) => kinds.includes(entry.kind)).map(({ kind, icon: Icon }) => {
        const detail = kindDetail(plugin, kind);
        return (
          <span
            key={kind}
            title={detail}
            className="flex items-center gap-1 rounded-md border border-border px-1.5 py-px text-[11px] text-muted-foreground"
          >
            <Icon aria-hidden className="size-3 text-icon" />
            <span className="sr-only">{detail}</span>
            <span aria-hidden>{t(KINDS.find((entry) => entry.kind === kind)?.label ?? "plugins.kind.capability")}</span>
          </span>
        );
      })}
    </span>
  );
}

/** All, or only the plugins of one kind. */
function KindFilter({ kind, onKind }: { kind: PluginKind | null; onKind: (kind: PluginKind | null) => void }) {
  const chip = (active: boolean) =>
    cn(
      "flex items-center gap-1.5 rounded-lg border px-2 py-1 text-ui-sm transition-colors [&_svg]:size-3.5 [&_svg]:text-icon",
      active
        ? "border-transparent bg-accent text-accent-foreground"
        : "border-border text-muted-foreground hover:bg-accent/60",
    );
  return (
    // biome-ignore lint/a11y/useSemanticElements: a row of toggle buttons, not a form's fieldset
    <div role="group" aria-label={t("plugins.kind.filter")} className="mb-2 flex flex-wrap gap-1.5">
      <button type="button" aria-pressed={kind === null} onClick={() => onKind(null)} className={chip(kind === null)}>
        {t("plugins.kind.all")}
      </button>
      {KINDS.map(({ kind: value, icon: Icon, label }) => (
        <button
          key={value}
          type="button"
          aria-pressed={kind === value}
          onClick={() => onKind(kind === value ? null : value)}
          className={chip(kind === value)}
        >
          <Icon aria-hidden />
          {t(label)}
        </button>
      ))}
    </div>
  );
}

function Discover({ query, onInstall }: { query: string; onInstall: (spec: string) => void }) {
  const search = usePluginSearch(query);
  return (
    <Loaded query={search}>
      {(result) => {
        const available = result.plugins.filter((plugin) => !plugin.installed);
        return available.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {query ? t("plugins.noneFound") : t("plugins.haveAll")}
          </p>
        ) : (
          <ul>
            {available.map((plugin) => (
              <li key={plugin.name} className="flex items-center gap-3 border-b border-border/70 py-3 last:border-b-0">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
                  {plugin.community ? (
                    <Users aria-hidden className="size-(--icon) text-icon" />
                  ) : (
                    <ShieldCheck aria-hidden className="size-(--icon) text-icon" />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2 text-ui">
                    {displayName(plugin.short_name, plugin.description)}
                  </span>
                  <span className="block truncate text-sm text-muted-foreground">{plugin.description}</span>
                  <RepositoryLine url={repositoryOfEntry(plugin)} />
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="rounded-md border border-border px-1.5 text-[11px] text-muted-foreground">
                    {plugin.source === "contrib"
                      ? t("plugins.contrib")
                      : plugin.community
                        ? t("plugins.community")
                        : t("plugins.firstParty")}
                  </span>
                  <span aria-hidden className={VERSION} />
                </span>
                {sourceLink(repositoryOfEntry(plugin), plugin.name)}
                <Button size="sm" variant="outline" onClick={() => onInstall(plugin.install_spec)}>
                  {t("plugins.install")}
                </Button>
              </li>
            ))}
          </ul>
        );
      }}
    </Loaded>
  );
}
