// Settings → Plugins, as in a desktop chat app: "Yours" lists what's installed, "Discover" what can be (first-party
// and community plugins), "Add" installs from a name or a git URL. Installing runs code, so each step asks first.
import { ExternalLink, Package, Plus, Search, ShieldCheck, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { useChangePlugin, usePluginSearch, usePlugins } from "@/api/queries";
import { Loaded } from "@/components/common";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

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

/** A catalog entry's repository: first-party plugins live in the core repository's `plugins/`. */
function repositoryOfEntry(entry: { name: string; source: string; install_spec: string }): string | null {
  return entry.source === "first-party" ? `${CORE}/tree/main/plugins/${entry.name}` : repositoryOf(entry.install_spec);
}

/** `https://github.com/o/r/tree/main/plugins/x` → `github.com/o/r`: which repository it comes from. */
export function repositoryLabel(url: string): string {
  const { host, pathname } = new URL(url);
  return [host, ...pathname.split("/").filter(Boolean).slice(0, 2)].join("/");
}

/** Where a plugin's code lives: the backend's `source_url` when it knows it, else the catalog's install spec. */
function sourceLink(url: string | null | undefined, name: string) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      aria-label={t("plugins.source", { name })}
      title={url}
      className="flex size-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"
    >
      <ExternalLink aria-hidden className="size-4" />
    </a>
  );
}

/** "from github.com/owner/repo", linking to it; "source unknown" until the backend says. */
function RepositoryLine({ url }: { url: string | null | undefined }) {
  return url ? (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="block truncate text-xs text-muted-foreground/80 hover:text-foreground hover:underline"
    >
      {t("plugins.from", { repository: repositoryLabel(url) })}
    </a>
  ) : (
    <span className="block text-xs text-muted-foreground/60">{t("plugins.sourceUnknown")}</span>
  );
}

type Pending = { action: "add"; spec: string; label: string } | { action: "remove"; name: string };

export function PluginsSection() {
  const [tab, setTab] = useState<"yours" | "discover">("yours");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [spec, setSpec] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const change = useChangePlugin();
  const tabClass = (active: boolean) =>
    cn(
      "rounded-lg px-3 py-1 text-sm transition-colors hover:bg-accent/70",
      active ? "bg-accent text-foreground" : "text-muted-foreground",
    );
  const confirm = () => {
    if (!pending) return;
    change.mutate(pending.action === "add" ? { add: pending.spec } : { remove: pending.name }, {
      onSuccess: () => {
        setPending(null);
        setAdding(false);
        setSpec("");
      },
    });
  };
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
        <label className="ml-auto flex items-center gap-2 rounded-lg bg-input px-2.5 py-1 text-muted-foreground">
          <Search aria-hidden className="size-4" />
          <span className="sr-only">{t("plugins.search")}</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("search.open")}
            className="w-36 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
          />
        </label>
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
            if (spec.trim()) setPending({ action: "add", spec: spec.trim(), label: spec.trim() });
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
              placeholder="heinzel-acme @ git+https://github.com/acme/heinzel-acme"
              className="h-8 min-w-0 flex-1 rounded-lg border border-border bg-input px-2.5 font-mono text-xs outline-none focus:border-muted-foreground/50"
            />
            <Button type="submit" size="sm" disabled={!spec.trim()}>
              {t("plugins.install")}
            </Button>
          </div>
        </form>
      )}
      {pending && (
        <div
          role="alertdialog"
          aria-label={t("plugins.confirmTitle")}
          className="mb-4 rounded-xl border border-warn/50 bg-warn/5 p-3 text-sm"
        >
          <p className="font-medium">
            {pending.action === "add" ? t("plugins.confirmAdd") : t("plugins.confirmRemove", { name: pending.name })}
          </p>
          {pending.action === "add" && (
            <code className="mt-1 block font-mono text-xs break-all text-muted-foreground">{pending.spec}</code>
          )}
          <p className="mt-1 text-xs text-muted-foreground">{t("plugins.restartNote")}</p>
          {change.error && <p className="mt-1 text-xs text-bad">{change.error.message}</p>}
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={confirm} disabled={change.isPending}>
              {change.isPending
                ? t("plugins.working")
                : pending.action === "add"
                  ? t("plugins.install")
                  : t("plugins.remove")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setPending(null)} disabled={change.isPending}>
              {t("plugins.cancel")}
            </Button>
          </div>
        </div>
      )}
      {tab === "yours" ? <Yours query={query} onRemove={(name) => setPending({ action: "remove", name })} /> : null}
      {tab === "discover" ? (
        <Discover query={query} onInstall={(spec, label) => setPending({ action: "add", spec, label })} />
      ) : null}
    </>
  );
}

function Yours({ query, onRemove }: { query: string; onRemove: (name: string) => void }) {
  const plugins = usePlugins();
  const catalog = usePluginSearch("");
  const known = new Map((catalog.data?.plugins ?? []).map((plugin) => [plugin.name, plugin]));
  return (
    <Loaded query={plugins}>
      {(list) => {
        const shown = list.filter((plugin) => plugin.distribution.toLowerCase().includes(query.toLowerCase()));
        return shown.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">{t("plugins.none")}</p>
        ) : (
          <ul>
            {shown.map((plugin) => {
              const entry = known.get(plugin.distribution);
              const source = entry
                ? entry.community
                  ? t("plugins.community")
                  : t("plugins.firstParty")
                : t("plugins.private");
              const url =
                ("source_url" in plugin ? (plugin as { source_url?: string | null }).source_url : null) ??
                (entry ? repositoryOfEntry(entry) : null);
              return (
                <li
                  key={plugin.distribution}
                  className="flex items-center gap-3 border-b border-border/70 py-3 last:border-b-0"
                >
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
                    <Package aria-hidden className="size-(--icon) text-foreground/70" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 text-ui">
                      {entry?.short_name ?? plugin.distribution.replace(/^heinzel-/, "")}
                      <span className="rounded-md border border-border px-1.5 text-[11px] text-muted-foreground">
                        {source}
                      </span>
                      <span className="text-xs text-muted-foreground">v{plugin.version}</span>
                    </span>
                    <span className="block truncate text-sm text-muted-foreground">
                      {entry?.description ?? t("plugins.modules", { modules: plugin.modules.join(", ") || "—" })}
                    </span>
                    <RepositoryLine url={url} />
                  </span>
                  {sourceLink(url, plugin.distribution)}
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => onRemove(plugin.distribution)}
                    aria-label={t("plugins.removeName", { name: plugin.distribution })}
                    title={t("plugins.remove")}
                    className="text-muted-foreground"
                  >
                    <Trash2 aria-hidden />
                  </Button>
                </li>
              );
            })}
          </ul>
        );
      }}
    </Loaded>
  );
}

function Discover({ query, onInstall }: { query: string; onInstall: (spec: string, label: string) => void }) {
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
                    <Users aria-hidden className="size-(--icon) text-foreground/70" />
                  ) : (
                    <ShieldCheck aria-hidden className="size-(--icon) text-foreground/70" />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2 text-ui">
                    {plugin.short_name}
                    <span className="rounded-md border border-border px-1.5 text-[11px] text-muted-foreground">
                      {plugin.community ? t("plugins.community") : t("plugins.firstParty")}
                    </span>
                  </span>
                  <span className="block truncate text-sm text-muted-foreground">{plugin.description}</span>
                  <RepositoryLine url={repositoryOfEntry(plugin)} />
                </span>
                {sourceLink(repositoryOfEntry(plugin), plugin.name)}
                <Button size="sm" variant="outline" onClick={() => onInstall(plugin.install_spec, plugin.name)}>
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
