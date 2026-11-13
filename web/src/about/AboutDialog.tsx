// About Heinzelmännchen: what is installed, in one dialog, opened from the footer version or the desktop app menu.
// The facts are `hz system about`'s (the same report, the same copied lines), plus what only this page knows: the web
// build it runs and the desktop app it runs in.
import { useQuery } from "@tanstack/react-query";
import { Copy, ExternalLink, FileText, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { SchemaAboutDaemon, SchemaAboutReport } from "@/api/client";
import { useAbout, useInfo } from "@/api/queries";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { format, t } from "@/i18n";
import { pageEntrypoints, webBuildOf } from "@/updates/webBuild";
import { onMenuAction } from "./menu";

const REPOSITORY = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel";

type Desktop = { version: string; path: string };
type Bridge = { about?: () => Promise<Desktop> };

const desktopBridge = (): Bridge | undefined => (window as Window & { heinzelDesktop?: Bridge }).heinzelDesktop;

const isFirstParty = (repository: string | null | undefined) =>
  repository?.startsWith("https://github.com/helmut-hoffer-von-ankershoffen/") === true;

const DAEMON_STATES = {
  running: "about.daemon.running",
  paused: "about.daemon.paused",
  stopped: "about.daemon.stopped",
  "not installed": "about.daemon.notInstalled",
  unknown: "about.daemon.unknown",
} as const;

/** The daemon in a few words: its state, and the hz it runs when known. */
export function daemonText(daemon: SchemaAboutDaemon): string {
  const state = t(DAEMON_STATES[daemon.state]);
  return daemon.version ? t("about.daemonVersion", { state, version: daemon.version }) : state;
}

/** `hz system about`'s lines, then this page's web build and the desktop app it runs in. */
export function detailsText(report: SchemaAboutReport, page: string | null, desktop: Desktop | null): string {
  const lines = [...report.diagnostics];
  if (page !== report.web_build) lines.push(`web build of this page ${page ?? "development"}`);
  if (desktop) lines.push(`desktop app running ${desktop.version} ${desktop.path}`);
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

function usePageBuild() {
  return useQuery({
    queryKey: ["web", "page-build"],
    queryFn: () => webBuildOf(pageEntrypoints()),
    staleTime: Infinity,
  });
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-0.5">
      <dt className="w-28 shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

const Note = ({ children }: { children: React.ReactNode }) => (
  <span className="block text-xs text-muted-foreground">{children}</span>
);

/** Mounted once; opens on the `about` menu action and copies the diagnostics on `copy`. */
export function AboutDialog() {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const info = useInfo();
  const about = useAbout();
  const desktop = useDesktop(true);
  const page = usePageBuild();
  const [copied, setCopied] = useState(false);
  const titleId = useId();
  const report = about.data;
  const details = report ? detailsText(report, page.data ?? null, desktop.data ?? null) : null;
  const { refetch } = about;
  useEffect(
    () =>
      onMenuAction(["about", "copy"], (action) => {
        if (action === "about") {
          if (!dialog.current) {
            returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            setOpen(true);
            void refetch(); // the daemon and the plugins as they are now, not as they were when the app started
          }
        } else if (details) void navigator.clipboard.writeText(details);
      }),
    [details, refetch],
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
    if (!details) return;
    await navigator.clipboard.writeText(details);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  const repositories = new Map((info.data?.plugins ?? []).map((plugin) => [plugin.name, plugin.repository]));
  const latest = info.data?.latest_release;
  const pageBuild = page.data ?? null;
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
      <div className="flex max-h-full w-[min(34rem,100%)] flex-col overflow-hidden rounded-xl border border-border bg-popover text-sm shadow-2xl">
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
          {report ? (
            <>
              <dl className="mt-4">
                <Row label={t("about.version")}>
                  hz {report.version}
                  {report.installed && <Note>{t("about.installedOn", { date: format.date(report.installed) })}</Note>}
                </Row>
                {latest && latest !== report.version && <Row label={t("about.latest")}>{latest}</Row>}
                <Row label={t("about.webBuild")}>
                  <span className="font-mono">{pageBuild ?? t("about.webBuildDev")}</span>
                  {report.web_build && pageBuild && report.web_build !== pageBuild && (
                    <Note>{t("about.webBuildServed", { build: report.web_build })}</Note>
                  )}
                </Row>
                <Row label={t("about.desktop")}>
                  {desktop.data ? (
                    <>
                      {desktop.data.version}
                      <Note>{desktop.data.path}</Note>
                    </>
                  ) : report.desktop ? (
                    t("about.desktopInstalled", { version: report.desktop })
                  ) : (
                    t("about.notInstalled")
                  )}
                </Row>
                <Row label={t("about.installation")}>
                  {t("about.installationValue", { installation: report.installation, source: report.source })}
                </Row>
                <Row label={t("about.channel")}>{report.channel}</Row>
                <Row label={t("about.daemon")}>
                  {daemonText(report.daemon)}
                  {report.daemon.outdated && <Note>{t("about.daemonOutdated")}</Note>}
                </Row>
                <Row label={t("about.python")}>{report.python_version}</Row>
                <Row label={t("about.platform")}>{report.platform}</Row>
              </dl>
              <h3 className="mt-4 mb-1 font-medium">{t("about.plugins")}</h3>
              {report.plugins.length === 0 ? (
                <p className="text-muted-foreground">{t("about.noPlugins")}</p>
              ) : (
                <table className="w-full text-left text-xs">
                  <thead className="text-muted-foreground">
                    <tr>
                      <th className="py-1 pr-2 font-normal">{t("about.pluginName")}</th>
                      <th className="py-1 pr-2 font-normal">{t("about.pluginVersion")}</th>
                      <th className="py-1 pr-2 font-normal">{t("about.pluginSource")}</th>
                      <th className="py-1 font-normal">{t("about.pluginInstalled")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.plugins.map((plugin) => (
                      <tr key={plugin.name} className="border-b border-border align-top">
                        <td className="py-1 pr-2 break-all">{plugin.name}</td>
                        <td className="py-1 pr-2">
                          {plugin.version}
                          <span className="block text-muted-foreground">
                            {plugin.pinned ? t("about.pinned", { pin: plugin.pinned }) : t("about.follows")}
                          </span>
                        </td>
                        <td className="py-1 pr-2">
                          {plugin.source}
                          {isFirstParty(repositories.get(plugin.name)) && (
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
            <p className="mt-4 text-muted-foreground">{about.isError ? t("about.unavailable") : t("about.loading")}</p>
          )}
        </div>
        <footer className="shrink-0 px-4 pb-3">
          <PaneActions>
            <PaneAction key="copy" icon={Copy} disabled={!details} onClick={() => void copy()}>
              {copied ? t("about.copied") : t("about.copy")}
            </PaneAction>
            {report && (
              <PaneAction key="release" icon={FileText} href={`${REPOSITORY}/releases/tag/v${report.version}`}>
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
