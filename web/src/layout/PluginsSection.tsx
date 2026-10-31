// Settings → Plugins, as in a desktop chat app: "Installed" lists what's installed, "Discover" what can be (first-party
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
import { useEffect, useId, useRef, useState } from "react";
import type { SchemaPluginCandidate, SchemaPluginChange, SchemaPluginInfo } from "@/api/client";
import {
  type PluginChangeRequest,
  pluginIdentity,
  readPluginOperation,
  useChangePlugin,
  usePluginOperation,
  usePluginPreview,
  usePluginSearch,
  usePluginShowcase,
  usePlugins,
} from "@/api/queries";
import { Loaded } from "@/components/common";
import { Button } from "@/components/ui/button";
import { SearchField } from "@/components/ui/search-field";
import { type MessageKey, t } from "@/i18n";
import { cn } from "@/lib/utils";
import type { RestartObservation } from "@/lifecycle/controller";
import { RestartAndApply } from "@/lifecycle/RestartAndApply";
import { displayName } from "./settingsSections";

type PluginKind = NonNullable<SchemaPluginInfo["kinds"]>[number];

/** Label the actual source repository; catalog membership and reserved package names establish no provenance. */
export function sourceLabel(
  plugin: Pick<SchemaPluginInfo, "source_url" | "homepage">,
  privateRepository?: boolean | null,
  sourceUrl?: string | null,
): string {
  const repository = knownRepository(sourceUrl ?? plugin.source_url ?? plugin.homepage);
  if (repository === "heinzel" || repository === "heinzel-plugins") return t("plugins.firstParty");
  if (repository === "heinzel-contrib") return t("plugins.community");
  if (privateRepository === true) return t("plugins.private");
  if (privateRepository === false) return t("plugins.custom");
  return t("plugins.sourceUnknown");
}

/** Compare complete GitHub repository identities; a similarly prefixed fork is a different source. */
function knownRepository(sourceUrl: string | null | undefined): string | null {
  if (!sourceUrl) return null;
  try {
    const source = new URL(sourceUrl);
    const [owner, repository] = source.pathname.slice(1).toLowerCase().split("/");
    if (
      source.protocol !== "https:" ||
      source.hostname !== "github.com" ||
      source.port ||
      source.username ||
      source.password ||
      owner !== "helmut-hoffer-von-ankershoffen"
    )
      return null;
    const name = repository?.replace(/\.git$/, "");
    return name === "heinzel" || name === "heinzel-plugins" || name === "heinzel-contrib" ? name : null;
  } catch {
    return null;
  }
}

/** The head of a module's page, the same everywhere: its name with the kinds of what it adds on the left, and on
 *  the right, on the same line, where it comes from (First-Party, Community, Private) and its version. */
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
                {sourceLabel(plugin, plugin.private_repository, plugin.source_url ?? plugin.homepage)}
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
  return repositoryOf(entry.install_spec);
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
  | { action: "add"; candidate: SchemaPluginCandidate; operationId: string }
  | { action: "remove" | "enable" | "disable"; name: string };

function request(pending: Pending): PluginChangeRequest {
  switch (pending.action) {
    case "add":
      // A first-party plugin is added by the name asked for; its git requirement is refused as a spec.
      return {
        add: pending.candidate.first_party ? pending.candidate.spec : pending.candidate.requirement,
        operationId: pending.operationId,
      };
    case "remove":
      return { remove: pending.name };
    case "enable":
      return { enable: pending.name };
    case "disable":
      return { disable: pending.name };
  }
}

const RECEIPT_STORAGE_KEY = "hz.plugin-add-operation.v1";
const RESTART_STORAGE_KEY = "hz.plugin-add-restart.v1";

function validRestartObservation(value: unknown): value is RestartObservation {
  if (!value || typeof value !== "object") return false;
  const observation = value as Partial<RestartObservation>;
  return (
    Array.isArray(observation.retired) &&
    observation.retired.length > 0 &&
    observation.retired.every((instance) => typeof instance === "string" && instance.trim()) &&
    typeof observation.version === "string" &&
    observation.version.trim().length > 0 &&
    Array.isArray(observation.settings) &&
    observation.settings.every(
      (setting) =>
        setting &&
        typeof setting.section === "string" &&
        typeof setting.key === "string" &&
        Object.hasOwn(setting, "value"),
    )
  );
}

function restoredPluginFlow(): {
  receiptId: string | null;
  restartObservation: RestartObservation | null;
  blocked: boolean;
} {
  try {
    const receiptId = window.sessionStorage.getItem(RECEIPT_STORAGE_KEY);
    const restart = window.sessionStorage.getItem(RESTART_STORAGE_KEY);
    if (!restart) return { receiptId, restartObservation: null, blocked: false };
    const saved: unknown = JSON.parse(restart);
    if (!saved || typeof saved !== "object") return { receiptId, restartObservation: null, blocked: true };
    const record = saved as { operation_id?: unknown; observation?: unknown };
    if (
      typeof record.operation_id !== "string" ||
      record.operation_id !== receiptId ||
      !validRestartObservation(record.observation)
    )
      return { receiptId, restartObservation: null, blocked: true };
    return { receiptId, restartObservation: record.observation, blocked: false };
  } catch {
    return { receiptId: null, restartObservation: null, blocked: true };
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
      `${sourceLabel({ source_url: candidate.url }, candidate.private)}${candidate.private === true ? `, ${t("plugins.privateRepository")}` : ""}`,
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
  const [restored] = useState(restoredPluginFlow);
  const [receiptId, setReceiptId] = useState<string | null>(restored.receiptId);
  const [restartObservation, setRestartObservation] = useState<RestartObservation | null>(restored.restartObservation);
  const [restartBlocked, setRestartBlocked] = useState(restored.blocked);
  const operation = usePluginOperation(receiptId);
  const [resolveArmed, setResolveArmed] = useState(false);
  const pluginRestartRequired = useRef(false);
  const completed = useRef<SchemaPluginChange | null>(null);
  const attempted = useRef(false);
  const preview = usePluginPreview();
  const change = useChangePlugin();
  const resolveReceipt = () => {
    if (!resolveArmed) {
      setResolveArmed(true);
      return;
    }
    try {
      window.sessionStorage.removeItem(RECEIPT_STORAGE_KEY);
      window.sessionStorage.removeItem(RESTART_STORAGE_KEY);
      setReceiptId(null);
      setRestartObservation(null);
      setRestartBlocked(false);
      setPending(null);
      setResolveArmed(false);
    } catch {
      setResolveArmed(true);
    }
  };
  useEffect(() => {
    const saved = operation.data;
    if (!saved) return;
    const candidate = saved.result?.candidate ?? saved.candidate;
    setPending({ action: "add", candidate, operationId: saved.operation_id });
    if (saved.status === "completed" && saved.result) {
      completed.current = saved.result;
      pluginRestartRequired.current = saved.result.restart_required;
    }
  }, [operation.data]);
  const tabClass = (active: boolean) =>
    cn(
      "rounded-lg px-3 py-1 text-sm transition-colors hover:bg-accent/70",
      active ? "bg-accent text-foreground" : "text-muted-foreground",
    );
  const show = (value: string) => {
    if (change.isPending) return;
    if (restartObservation || restartBlocked) return;
    if (receiptId && operation.data?.status !== "previewed") return;
    change.reset();
    completed.current = null;
    attempted.current = false;
    setPluginProgress(null);
    try {
      window.sessionStorage.removeItem(RECEIPT_STORAGE_KEY);
      window.sessionStorage.removeItem(RESTART_STORAGE_KEY);
    } catch {
      // Confirmation fails closed if this tab cannot persist the next receipt before sending the request.
    }
    setReceiptId(null);
    setRestartObservation(null);
    preview.mutate(value, {
      onSuccess: (answer) => {
        if (answer.candidate && answer.operation_id) {
          setReceiptId(answer.operation_id);
          setPending({ action: "add", candidate: answer.candidate, operationId: answer.operation_id });
        }
      },
    });
  };
  const ask = (value: Pending) => {
    if (change.isPending) return;
    change.reset();
    completed.current = null;
    attempted.current = false;
    setPluginProgress(null);
    preview.reset();
    setPending(value);
  };
  const confirm = async (signal: AbortSignal) => {
    if (!pending) return false;
    if (completed.current) return completed.current.restart_required;
    if (attempted.current) throw new Error(t("plugins.resultUnknown"));
    setPluginProgress(null);
    const operation = request(pending);
    if ("add" in operation) {
      if (!operation.operationId) throw new Error(t("plugins.receiptStorageUnavailable"));
      try {
        window.sessionStorage.setItem(RECEIPT_STORAGE_KEY, operation.operationId);
      } catch {
        throw new Error(t("plugins.receiptStorageUnavailable"));
      }
      if (receiptId !== operation.operationId) setReceiptId(operation.operationId);
      const status = await readPluginOperation(operation.operationId);
      if (status.status === "running") throw new Error(t("plugins.operationRunning"));
      if (status.status === "unknown") throw new Error(t("plugins.operationUnknown"));
      if (status.status === "completed" && status.result) {
        completed.current = status.result;
        pluginRestartRequired.current = status.result.restart_required;
        return status.result.restart_required;
      }
      attempted.current = true;
    }
    // A lost removal response is also uncertain. Require a fresh, explicit choice after status reconciliation.
    if ("remove" in operation) attempted.current = true;
    const answer = await change.mutateAsync({ ...operation, signal, onProgress: setPluginProgress });
    if (!answer.done) throw new Error(t("plugins.notCompleted"));
    completed.current = answer;
    pluginRestartRequired.current = answer.restart_required;
    if (pending.action === "add") setTab("yours");
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
      {restartBlocked && (
        <div className="mb-4 space-y-2 rounded-xl border border-warn/50 bg-warn/5 p-3 text-sm">
          <p role="alert">{t("plugins.operationUnavailable")}</p>
          <p className="text-xs text-muted-foreground">{t("plugins.operationUnknownHelp")}</p>
          <Button size="sm" variant="outline" onClick={resolveReceipt}>
            {resolveArmed ? t("plugins.operationResolveConfirm") : t("plugins.operationResolve")}
          </Button>
        </div>
      )}
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
      {receiptId && operation.isError && (
        <div className="mb-4 space-y-2 rounded-xl border border-warn/50 bg-warn/5 p-3 text-sm">
          <p role="alert">{t("plugins.operationUnavailable")}</p>
          <p className="text-xs text-muted-foreground">{t("plugins.operationUnknownHelp")}</p>
          <Button size="sm" variant="outline" onClick={resolveReceipt}>
            {resolveArmed ? t("plugins.operationResolveConfirm") : t("plugins.operationResolve")}
          </Button>
        </div>
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
            {pending.action === "add" && operation.data?.status === "running" ? (
              <p role="status" className="mt-2 text-xs text-muted-foreground">
                {t("plugins.operationRunning")}
              </p>
            ) : pending.action === "add" && operation.data?.status === "unknown" ? (
              <div className="mt-2 space-y-2">
                <p role="alert" className="text-xs text-bad">
                  {t("plugins.operationUnknown")}
                </p>
                <p className="text-xs text-muted-foreground">{t("plugins.operationUnknownHelp")}</p>
                <Button size="sm" variant="outline" onClick={resolveReceipt}>
                  {resolveArmed ? t("plugins.operationResolveConfirm") : t("plugins.operationResolve")}
                </Button>
              </div>
            ) : (
              <>
                <RestartAndApply
                  target={t("plugins.title")}
                  changes={[
                    `${actionText(pending.action)}: ${pending.action === "add" ? (pending.candidate.distribution ?? pending.candidate.spec) : pending.name}`,
                  ]}
                  preserve={confirm}
                  initialObservation={pending.action === "add" ? (restartObservation ?? undefined) : undefined}
                  onApplied={() => {
                    if (pending.action === "add") {
                      try {
                        window.sessionStorage.removeItem(RECEIPT_STORAGE_KEY);
                        window.sessionStorage.removeItem(RESTART_STORAGE_KEY);
                        setReceiptId(null);
                        setRestartObservation(null);
                        setRestartBlocked(false);
                      } catch {
                        // Keeping the receipt is safe; the next visit will read the completed result.
                      }
                    }
                    setPending(null);
                    setAdding(false);
                    setSpec("");
                    if (pluginRestartRequired.current) (reload ?? (() => window.location.reload()))();
                  }}
                  options={{
                    ...(pending.action === "add" || pending.action === "remove"
                      ? { preserveTimeout: 20 * 60_000 }
                      : {}),
                    ...(restartInterval ? { interval: restartInterval } : {}),
                    ...(pending.action === "add"
                      ? {
                          onObservation: (observation: RestartObservation) => {
                            window.sessionStorage.setItem(
                              RESTART_STORAGE_KEY,
                              JSON.stringify({ operation_id: pending.operationId, observation }),
                            );
                            setRestartObservation(observation);
                          },
                        }
                      : {}),
                  }}
                />
                {(pending.action !== "add" || operation.data?.status === "previewed") && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (pending.action === "add") {
                        setReceiptId(null);
                        setRestartObservation(null);
                      }
                      setPending(null);
                    }}
                    disabled={change.isPending}
                  >
                    {t("plugins.cancel")}
                  </Button>
                )}
              </>
            )}
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
              <ul className="@container">
                {shown.map((plugin) => {
                  const entry = known.get(plugin.distribution);
                  const url = plugin.source_url ?? plugin.homepage ?? null;
                  const source = sourceLabel(plugin, plugin.private_repository, url);
                  const enabled = plugin.enabled ?? true;
                  return (
                    <li
                      key={plugin.distribution}
                      className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-3 border-b border-border/70 py-3 last:border-b-0 @md:flex @md:flex-wrap"
                    >
                      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
                        <Package aria-hidden className="size-(--icon) text-icon" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex min-w-0 flex-wrap items-center gap-2 text-ui [overflow-wrap:anywhere]">
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
                      {/* Narrow settings panels keep provenance and controls below the name instead of clipping. */}
                      <div className="col-span-2 flex min-w-0 flex-wrap items-center justify-end gap-2 @md:contents">
                        <span className="mr-auto flex min-w-0 flex-wrap items-center gap-2 @md:mr-0 @md:shrink-0">
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
                          onClick={() =>
                            onChange({ action: enabled ? "disable" : "enable", name: plugin.distribution })
                          }
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
                      </div>
                      <div className="col-span-2 min-w-0 @md:basis-full">
                        <PluginGallery
                          spec={installedGallerySpec(plugin) ?? entry?.install_spec ?? plugin.distribution}
                          name={displayName(plugin.name, plugin.summary)}
                          privateSource={plugin.private_repository === true}
                        />
                      </div>
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
  const installed = usePlugins();
  return (
    <Loaded query={search}>
      {(result) => {
        const installedNames = installed.data
          ? new Set(installed.data.map((plugin) => pluginIdentity(plugin.distribution)))
          : null;
        const available = result.plugins.filter((plugin) =>
          installedNames ? !installedNames.has(pluginIdentity(plugin.name)) : !plugin.installed,
        );
        return available.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {query ? t("plugins.noneFound") : t("plugins.haveAll")}
          </p>
        ) : (
          <ul>
            {available.map((plugin) => (
              <li key={plugin.name} className="border-b border-border/70 py-3 last:border-b-0">
                <div className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-3">
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
                  <div className="col-span-2 flex min-w-0 flex-wrap items-center gap-2">
                    <span className="rounded-md border border-border px-1.5 text-[11px] text-muted-foreground">
                      {sourceLabel({ source_url: repositoryOfEntry(plugin) }, plugin.private_repository)}
                    </span>
                    {sourceLink(repositoryOfEntry(plugin), plugin.name)}
                    <Button size="sm" variant="outline" onClick={() => onInstall(plugin.install_spec)}>
                      {t("plugins.install")}
                    </Button>
                  </div>
                </div>
                <PluginGallery
                  spec={plugin.install_spec}
                  name={displayName(plugin.short_name, plugin.description)}
                  privateSource={plugin.private_repository === true}
                />
              </li>
            ))}
          </ul>
        );
      }}
    </Loaded>
  );
}

/** Recover an installed source's immutable project path without fetching or loading the plugin. */
export function installedGallerySpec(plugin: SchemaPluginInfo): string | null {
  const source = plugin.source_url ?? plugin.homepage;
  if (!source) return null;
  try {
    const url = new URL(source);
    if (url.protocol !== "https:" || url.hostname !== "github.com" || url.port || url.username || url.password)
      return null;
    const [owner, repository, tree, revision, ...path] = url.pathname.split("/").filter(Boolean);
    if (!owner || !repository || (tree && tree !== "tree")) return null;
    const identity = `${plugin.distribution} @ git+https://github.com/${owner}/${repository.replace(/\.git$/, "")}`;
    if (tree && revision) return `${identity}@${revision}${path.length ? `#subdirectory=${path.join("/")}` : ""}`;
    return identity;
  } catch {
    return null;
  }
}

/** Read owner-declared media only after a person asks to see the selected plugin. */
export function PluginGallery({
  spec,
  name,
  privateSource = false,
}: {
  spec: string;
  name: string;
  privateSource?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const galleryId = useId();
  return (
    <div className="mt-2 min-w-0">
      <Button
        size="sm"
        variant="ghost"
        aria-expanded={open}
        aria-controls={open ? galleryId : undefined}
        aria-label={t("plugins.screenshotsFor", { name })}
        onClick={() => setOpen((value) => !value)}
      >
        {t("plugins.screenshots")}
      </Button>
      {open && (
        <section
          id={galleryId}
          aria-label={t("plugins.galleryFor", { name })}
          className="mt-2 min-w-0 rounded-lg border border-border bg-muted/30 p-3"
        >
          {privateSource ? (
            <p className="text-sm text-muted-foreground">{t("plugins.privateGallery")}</p>
          ) : (
            <GalleryContents key={spec} spec={spec} />
          )}
        </section>
      )}
    </div>
  );
}

function GalleryContents({ spec }: { spec: string }) {
  const query = usePluginShowcase(spec);
  const [selected, setSelected] = useState(0);
  return (
    <Loaded query={query}>
      {(gallery) => {
        const shots = gallery.screenshots ?? [];
        const shot = shots[selected] ?? shots[0];
        return (
          <div className="min-w-0 space-y-3">
            {shot ? (
              <>
                <figure className="min-w-0">
                  <a
                    href={shot.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    aria-label={t("plugins.fullSizeFor", { caption: shot.caption })}
                  >
                    <img
                      src={shot.url}
                      alt={shot.caption}
                      referrerPolicy="no-referrer"
                      className="max-h-96 w-full rounded-md border border-border object-contain"
                    />
                  </a>
                  <figcaption className="mt-2 break-words text-sm text-muted-foreground">
                    <span className="mr-2 font-medium">{shot.surface.toUpperCase()}</span>
                    {shot.caption}
                  </figcaption>
                </figure>
                <fieldset className="flex min-w-0 flex-wrap gap-2">
                  <legend className="sr-only">{t("plugins.chooseScreenshot")}</legend>
                  {shots.map((image, index) => (
                    <button
                      key={`${image.url}-${image.caption}-${image.surface}`}
                      type="button"
                      aria-pressed={selected === index}
                      aria-label={`${image.surface.toUpperCase()}: ${image.caption}`}
                      onClick={() => setSelected(index)}
                      className={cn(
                        "min-h-11 rounded-md border border-border p-1",
                        selected === index && "ring-2 ring-ring",
                      )}
                    >
                      <img
                        src={image.url}
                        alt=""
                        loading="lazy"
                        referrerPolicy="no-referrer"
                        className="h-14 w-24 rounded-sm object-contain"
                      />
                      <span className="block text-xs">{image.surface.toUpperCase()}</span>
                    </button>
                  ))}
                </fieldset>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">{gallery.notice || t("plugins.noScreenshots")}</p>
            )}
            {(gallery.soundscapes?.length ?? 0) > 0 && (
              <div>
                <h4 className="text-sm font-medium">{t("plugins.soundscapes")}</h4>
                <p className="text-xs text-muted-foreground">{t("plugins.youtubeOnClick")}</p>
                <ul className="space-y-1">
                  {gallery.soundscapes?.map((sound) => (
                    <li key={sound.url}>
                      <a
                        href={sound.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="inline-flex min-h-11 items-center gap-2 break-words text-sm underline underline-offset-4"
                      >
                        {sound.title} · {t("plugins.watchSoundscape")}
                        <ExternalLink aria-hidden className="size-3 shrink-0" />
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        );
      }}
    </Loaded>
  );
}
