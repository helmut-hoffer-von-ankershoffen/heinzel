// About Heinzelmännchen: what is installed, in one dialog, opened from the footer version or the desktop app menu.
import { useQuery } from "@tanstack/react-query";
import { Copy, ExternalLink, FileText, X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { useInfo } from "@/api/queries";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { onMenuAction } from "./menu";

const REPOSITORY = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel";

type Desktop = { version: string; path: string };
type Bridge = { about?: () => Promise<Desktop> };

const desktopBridge = (): Bridge | undefined => (window as Window & { heinzelDesktop?: Bridge }).heinzelDesktop;

type Info = NonNullable<ReturnType<typeof useInfo>["data"]>;

/** The installed plugins, one row per distribution, as the API lists them. */
export const pluginsOf = (info: Info) => info.plugins ?? [];

/** The same lines as `hz --version`, plus the daemon and the desktop app. */
export function detailsText(info: Info, desktop: Desktop | null): string {
  const lines = [`hz ${info.version}`];
  if (info.repository) lines.push(`repository ${info.repository}`);
  lines.push(
    `python ${info.python_version} (${info.python_executable})`,
    `platform ${info.platform}`,
    `installation ${info.installation}`,
    `daemon running ${info.version}`,
  );
  if (desktop) lines.push(`desktop ${desktop.version} ${desktop.path}`);
  for (const plugin of pluginsOf(info))
    lines.push(
      `plugin ${plugin.name} ${plugin.version} ${plugin.source}${plugin.repository ? ` ${plugin.repository}` : ""}`,
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
  const info = useInfo();
  const desktop = useDesktop(true);
  const [copied, setCopied] = useState(false);
  const titleId = useId();
  const details = info.data ? detailsText(info.data, desktop.data ?? null) : null;
  useEffect(
    () =>
      onMenuAction(["about", "copy"], (action) => {
        if (action === "about") setOpen(true);
        else if (details) void navigator.clipboard.writeText(details);
      }),
    [details],
  );
  if (!open) return null;
  const close = () => setOpen(false);
  const copy = async () => {
    if (!details) return;
    await navigator.clipboard.writeText(details);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  const data = info.data;
  return createPortal(
    // biome-ignore lint/a11y/noStaticElementInteractions: a click on the backdrop closes it; Escape and the close button do for keyboards
    <div
      className="viewport-overlay fixed z-(--layer-modal) flex items-center justify-center bg-black/50 p-4"
      onMouseDown={close}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={(event) => event.key === "Escape" && close()}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex max-h-full w-[min(32rem,100%)] flex-col overflow-hidden rounded-xl border border-border bg-popover text-sm shadow-2xl"
      >
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <header className="flex items-center gap-3">
            <HeinzelAvatar view="head" label={t("app.name")} className="size-12" />
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
                <Row label={t("about.version")}>hz {data.version}</Row>
                {desktop.data && (
                  <Row label={t("about.desktop")}>
                    {desktop.data.version}
                    <span className="block text-xs text-muted-foreground">{desktop.data.path}</span>
                  </Row>
                )}
                <Row label={t("about.daemon")}>{t("about.daemonRunning", { version: data.version })}</Row>
                <Row label={t("about.python")}>{data.python_version}</Row>
                <Row label={t("about.platform")}>{data.platform}</Row>
                <Row label={t("about.installation")}>{data.installation}</Row>
                {data.latest_release && data.latest_release !== data.version && (
                  <Row label={t("about.latest")}>{data.latest_release}</Row>
                )}
              </dl>
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
                    </tr>
                  </thead>
                  <tbody>
                    {pluginsOf(data).map((plugin) => (
                      <tr key={plugin.name} className="border-t border-border">
                        <td className="py-1 pr-2 break-all">{plugin.name}</td>
                        <td className="py-1 pr-2">{plugin.version}</td>
                        <td className="py-1">{plugin.source}</td>
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
        <footer className="flex shrink-0 items-center gap-2 border-t border-border px-5 py-3">
          <Button size="sm" variant="outline" disabled={!details} onClick={() => void copy()}>
            <Copy aria-hidden />
            {copied ? t("about.copied") : t("about.copy")}
          </Button>
          {data && (
            <Button asChild size="icon" variant="ghost">
              <a
                href={`${REPOSITORY}/releases/tag/v${data.version}`}
                target="_blank"
                rel="noreferrer"
                aria-label={t("status.release")}
                title={t("status.release")}
              >
                <FileText aria-hidden />
              </a>
            </Button>
          )}
          <Button asChild size="icon" variant="ghost">
            <a
              href={REPOSITORY}
              target="_blank"
              rel="noreferrer"
              aria-label={t("about.repository")}
              title={t("about.repository")}
            >
              <ExternalLink aria-hidden />
            </a>
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="ml-auto"
            onClick={close}
            aria-label={t("op.close")}
            title={t("op.close")}
            autoFocus
          >
            <X aria-hidden />
          </Button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
