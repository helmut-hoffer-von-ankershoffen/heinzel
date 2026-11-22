// About Heinzelmännchen: what is installed, in one dialog, opened from the footer version or the desktop app menu.
import { useQuery } from "@tanstack/react-query";
import { Copy, ExternalLink, FileText, X } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, unwrap } from "@/api/client";
import { useInfo } from "@/api/queries";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { format, t } from "@/i18n";
import { loadedFrontend, surfaceDetails } from "@/lib/surfaceDiagnostics";
import { onMenuAction } from "./menu";

const REPOSITORY = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel";
const loadedAsset = loadedFrontend(document);

type Desktop = { version: string; path: string };
type Bridge = { about?: () => Promise<Desktop> };

const desktopBridge = (): Bridge | undefined => (window as Window & { heinzelDesktop?: Bridge }).heinzelDesktop;

type Info = NonNullable<ReturnType<typeof useInfo>["data"]>;
export type Client = "native" | "electron" | "browser";

/** Which client shows this page: the Electron app's bridge offers `about`, the native app's bridge does not. */
export const clientHere = (bridge: Bridge | undefined = desktopBridge()): Client =>
  bridge ? (bridge.about ? "electron" : "native") : "browser";

const clientLabel = (client: Client) =>
  t(client === "native" ? "about.nativeApp" : client === "electron" ? "about.electronApp" : "about.browser");

/** The desktop app's row: its variant and version (`Native app 0.9.53`) and path, from the API, else the bridge. */
export function desktopApp(info: Info, desktop: Desktop | null): { name: string; path: string } | null {
  const app = info.desktop_app;
  if (app) {
    const variant = app.variant ? clientLabel(app.variant) : t("about.desktop");
    return { name: app.version ? `${variant} ${app.version}` : variant, path: app.path };
  }
  return desktop ? { name: `${clientLabel("electron")} ${desktop.version}`, path: desktop.path } : null;
}

const isFirstParty = (repository: string | null) =>
  repository?.startsWith("https://github.com/helmut-hoffer-von-ankershoffen/") === true;

/** The installed plugins, one row per distribution, as the API lists them. */
export const pluginsOf = (info: Info) => info.plugins ?? [];

/** The same lines as `hz --version`, plus the daemon and the desktop app. */
export function detailsText(
  info: Info,
  desktop: Desktop | null,
  frontend: string | null = null,
  surfaces: string[] = [],
): string {
  const lines = [`hz ${info.version}`];
  if (info.repository) lines.push(`repository ${info.repository}`);
  lines.push(
    `python ${info.python_version} (${info.python_executable})`,
    `platform ${info.platform}`,
    `installation ${info.installation}${info.installed ? ` (installed ${info.installed})` : ""}`,
    `daemon running ${info.version}`,
  );
  const app = desktopApp(info, desktop);
  if (app) lines.push(`desktop ${app.name} ${app.path}`);
  lines.push(`client ${clientLabel(clientHere())}`);
  if (frontend) lines.push(`loaded frontend entrypoint ${frontend}`);
  else if (desktop) lines.push("loaded frontend entrypoint unavailable");
  lines.push(...surfaces);
  for (const plugin of pluginsOf(info))
    lines.push(
      `plugin ${plugin.name} ${plugin.version} ${plugin.source}${plugin.installed ? ` installed ${plugin.installed}` : ""}${plugin.repository ? ` ${plugin.repository}` : ""}`,
    );
  return lines.join("\n");
}

function useDesktop(enabled: boolean) {
  return useQuery({
    queryKey: ["desktop", "about"],
    queryFn: () => desktopBridge()?.about?.() ?? Promise.resolve(null),
    enabled: enabled && Boolean(desktopBridge()?.about),
    staleTime: Infinity,
    retry: false,
  });
}

/** The desktop apps that connected to this workspace, as the workspace recorded them; read while About is open. */
function useConnectedClients(enabled: boolean) {
  return useQuery({
    queryKey: ["connected-clients"],
    queryFn: () => unwrap(api.GET("/connected-clients")),
    enabled,
    retry: false,
  });
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-0.5">
      <dt className="w-24 shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

/** Mounted once; opens on the `about` menu action and copies the details on `copy`. */
export function AboutDialog() {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const info = useInfo();
  const desktop = useDesktop(true);
  const clients = useConnectedClients(open);
  const [copied, setCopied] = useState(false);
  const titleId = useId();
  const app = info.data ? desktopApp(info.data, desktop.data ?? null) : null;
  const details = info.data ? detailsText(info.data, desktop.data ?? null, loadedAsset, surfaceDetails()) : null;
  const currentDetails = useCallback(
    () => (info.data ? detailsText(info.data, desktop.data ?? null, loadedAsset, surfaceDetails()) : null),
    [info.data, desktop.data],
  );
  useEffect(
    () =>
      onMenuAction(["about", "copy"], (action) => {
        if (action === "about") {
          if (!dialog.current) {
            returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            setOpen(true);
          }
        } else {
          const current = currentDetails();
          if (current) void navigator.clipboard.writeText(current);
        }
      }),
    [currentDetails],
  );
  useLayoutEffect(() => {
    const element = dialog.current;
    if (!open || !element) return;
    element.showModal();
    closeButton.current?.focus();
    return () => {
      element.close();
      if (returnFocus.current?.isConnected) returnFocus.current.focus();
    };
  }, [open]);
  if (!open) return null;
  const close = () => setOpen(false);
  const copy = async () => {
    const current = currentDetails();
    if (!current) return;
    await navigator.clipboard.writeText(current);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  const data = info.data;
  return createPortal(
    <dialog
      ref={dialog}
      data-hz-about-dialog=""
      aria-modal="true"
      aria-labelledby={titleId}
      className="viewport-overlay fixed z-(--layer-modal) m-0 flex max-h-none max-w-none items-center justify-center border-0 bg-transparent p-4 text-foreground backdrop:bg-black/50"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onClose={close}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          close();
        }
        if (event.key !== "Tab") return;
        const controls = event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled), a[href]");
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
    >
      {/* The native modal makes the background inert; this panel stops backdrop clicks. */}
      <div className="flex max-h-full w-[min(32rem,100%)] flex-col overflow-hidden rounded-xl border border-border bg-popover text-sm shadow-2xl">
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <header className="flex items-center gap-3">
            <HeinzelAvatar view="head" label={t("app.name")} className="size-12 overflow-hidden" />
            <div>
              <h2 id={titleId} className="text-lg font-medium">
                {t("app.name")}
              </h2>
              <p className="text-xs text-muted-foreground">{t("status.tagline")}</p>
            </div>
          </header>
          {data ? (
            <>
              <dl className="mt-4">
                <Row label={t("about.version")}>
                  hz {data.version}
                  {data.installed && (
                    <span className="block text-xs text-muted-foreground">
                      {t("about.installedOn", { date: format.date(data.installed) })}
                    </span>
                  )}
                </Row>
                {app && (
                  <Row label={t("about.desktop")}>
                    {app.name}
                    <span className="block text-xs text-muted-foreground">{app.path}</span>
                  </Row>
                )}
                <Row label={t("about.client")}>{clientLabel(clientHere())}</Row>
                <Row label={t("about.daemon")}>{t("about.daemonRunning", { version: data.version })}</Row>
                <Row label={t("about.python")}>{data.python_version}</Row>
                <Row label={t("about.platform")}>{data.platform}</Row>
                <Row label={t("about.installation")}>{data.installation}</Row>
                {data.latest_release && data.latest_release !== data.version && (
                  <Row label={t("about.latest")}>{data.latest_release}</Row>
                )}
              </dl>
              {clients.data && clients.data.length > 0 && (
                <>
                  <h3 className="mt-4 mb-1 font-medium">{t("about.clients")}</h3>
                  <ul className="text-xs" data-testid="connected-clients">
                    {clients.data.map((client) => (
                      <li key={`${client.flavor}-${client.machine}`} className="border-b border-border py-1">
                        <span className="font-medium">{client.machine}</span>{" "}
                        {t("about.clientDetails", {
                          flavor: client.flavor,
                          app: client.app_version,
                          backend: client.backend_version,
                          seen: format.date(client.last_seen),
                        })}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <h3 className="mt-4 mb-1 font-medium">{t("about.plugins")}</h3>
              {pluginsOf(data).length === 0 ? (
                <p className="text-muted-foreground">{t("about.noPlugins")}</p>
              ) : (
                <table className="w-full text-left text-xs">
                  <thead className="text-muted-foreground">
                    <tr>
                      <th className="py-1 font-normal">{t("about.pluginName")}</th>
                      <th className="py-1 font-normal">{t("about.pluginVersion")}</th>
                      <th className="py-1 font-normal">{t("about.pluginSource")}</th>
                      <th className="py-1 font-normal">{t("about.pluginInstalled")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pluginsOf(data).map((plugin) => (
                      <tr key={plugin.name} className="border-b border-border">
                        <td className="py-1 pr-2 break-all">{plugin.name}</td>
                        <td className="py-1 pr-2">{plugin.version}</td>
                        <td className="py-1 pr-2">
                          {plugin.source}
                          {isFirstParty(plugin.repository) && (
                            <span className="ml-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px]">
                              {t("about.firstParty")}
                            </span>
                          )}
                        </td>
                        <td className="py-1">{plugin.installed ? format.date(plugin.installed) : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          ) : (
            <p className="mt-4 text-muted-foreground">{info.isError ? t("about.unavailable") : t("about.loading")}</p>
          )}
        </div>
        <footer className="shrink-0 px-4 pb-3">
          <PaneActions>
            <PaneAction key="copy" icon={Copy} disabled={!details} onClick={() => void copy()}>
              {copied ? t("about.copied") : t("about.copy")}
            </PaneAction>
            {data && (
              <PaneAction key="release" icon={FileText} href={`${REPOSITORY}/releases/tag/v${data.version}`}>
                {t("status.release")}
              </PaneAction>
            )}
            <PaneAction key="repository" icon={ExternalLink} href={REPOSITORY}>
              {t("about.repository")}
            </PaneAction>
            <PaneAction key="close" ref={closeButton} icon={X} className="ml-auto" onClick={close} autoFocus>
              {t("op.close")}
            </PaneAction>
          </PaneActions>
        </footer>
      </div>
    </dialog>,
    document.body,
  );
}
