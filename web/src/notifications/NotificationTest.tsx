// "Send a test notification", honestly: `hz system notify test --native` answers how each way of reaching you stands
// (this app, the native notifier, the daemon), and this browser adds its own permission. Every way that is blocked
// gets the one button that fixes it, and after a fix, Try again. If nothing pops up outside the app, it says so.
import { BellRing, Check, RotateCcw, X } from "lucide-react";
import { useState } from "react";
import type { SchemaChannel, SchemaTested } from "@/api/client";
import { useOpenNotificationSettings, useTestNotice } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { type MessageKey, t } from "@/i18n";
import { runInTerminal } from "@/lib/terminal";
import { cn } from "@/lib/utils";
import { type DesktopState, setDesktop, useDesktopState } from "./desktop";

type Row = { key: string; label: string; ok: boolean; detail: string; action?: React.ReactNode };

const LABELS: Record<SchemaChannel["name"], MessageKey> = {
  web: "notify.channel.web",
  native: "notify.channel.native",
  daemon: "notify.channel.daemon",
};

const BROWSER: Record<DesktopState, MessageKey> = {
  on: "notify.browser.on",
  off: "notify.browser.off",
  denied: "notify.browser.denied",
  unsupported: "notify.browser.unsupported",
};

/** Whether anything shows the test outside this app: a native pop-up, this browser's, or the daemon's. */
export function popsUpOutside(result: SchemaTested, browser: DesktopState): boolean {
  const native = result.channels?.find((channel) => channel.name === "native")?.state;
  const daemon = result.channels?.find((channel) => channel.name === "daemon")?.state;
  return browser === "on" || native === "shown" || (native === "ready" && daemon === "ready");
}

/** `onTerminal` runs after a fix is typed into the terminal, for a host (the setup dialog) that covers the panel. */
export function NotificationTest({
  className,
  label,
  onTerminal,
}: {
  className?: string;
  label?: string;
  onTerminal?: () => void;
}) {
  const test = useTestNotice();
  const settings = useOpenNotificationSettings();
  const browser = useDesktopState();
  const [confirm, setConfirm] = useState<string | null>(null);
  const [fixed, setFixed] = useState(false);
  const result = test.data;
  const again = () => {
    setFixed(false);
    test.mutate();
  };
  const fix = (action: () => void) => () => {
    action();
    setFixed(true);
  };
  const channelAction = (channel: SchemaChannel) => {
    if (channel.fix === "open-settings") {
      return (
        <Button size="sm" variant="outline" onClick={fix(() => settings.mutate())} disabled={settings.isPending}>
          {t("notify.fix.openSettings")}
        </Button>
      );
    }
    if (channel.command) {
      const label =
        channel.fix === "install-notifier"
          ? t("notify.fix.installNotifier")
          : channel.fix === "install-daemon"
            ? t("notify.fix.installDaemon")
            : t("notify.fix.turnOn");
      return (
        <Button size="sm" variant="outline" onClick={() => setConfirm(channel.command ?? null)}>
          {label}
        </Button>
      );
    }
    return null;
  };
  const rows: Row[] = result
    ? [
        ...(result.channels ?? []).map((channel) => ({
          key: channel.name,
          label: t(LABELS[channel.name]),
          ok: channel.state === "shown" || channel.state === "ready",
          detail: `${t(`notify.state.channel.${channel.state}` as MessageKey)}: ${channel.detail}`,
          action: channelAction(channel),
        })),
        {
          key: "browser",
          label: t("notify.channel.browser"),
          ok: browser === "on",
          detail: t(BROWSER[browser]),
          action:
            browser === "off" ? (
              <Button size="sm" variant="outline" onClick={fix(() => void setDesktop(true))}>
                {t("notify.fix.allowBrowser")}
              </Button>
            ) : null,
        },
      ]
    : [];
  const outside = result ? popsUpOutside(result, browser) : true;
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={again} disabled={test.isPending}>
          {result ? <RotateCcw aria-hidden /> : <BellRing aria-hidden />}
          {result ? t("notify.tryAgain") : (label ?? t("notify.test"))}
        </Button>
        {fixed && <span className="text-xs text-muted-foreground">{t("notify.afterFix")}</span>}
      </div>
      {test.error && (
        <p role="alert" className="text-xs text-bad">
          {test.error.message}
        </p>
      )}
      {result && (
        <section aria-label={t("notify.result")} className="rounded-lg border border-border p-2 text-ui-sm">
          <p role="status" className={cn("mb-1.5 font-medium", outside ? "text-ok" : "text-warn")}>
            {result.native === "shown"
              ? t("notify.result.shown")
              : outside
                ? t("notify.result.outside")
                : t("notify.result.insideOnly")}
          </p>
          <ul className="flex flex-col gap-1.5">
            {rows.map((row) => (
              <li key={row.key} className="flex items-start gap-2">
                {row.ok ? (
                  <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-ok" />
                ) : (
                  <X aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
                )}
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{row.label}</span>
                  <span className="sr-only">{row.ok ? t("notify.works") : t("notify.blocked")}</span>
                  <span className="block text-muted-foreground">{row.detail}</span>
                </span>
                {row.action}
              </li>
            ))}
          </ul>
        </section>
      )}
      {settings.data && !settings.data.opened && <p className="text-xs text-warn">{settings.data.message}</p>}
      {confirm && (
        <div
          role="alertdialog"
          aria-label={t("notify.confirmTitle")}
          className="rounded-lg border border-warn p-2 text-ui-sm"
        >
          <p>{t("notify.confirmRun")}</p>
          <code className="mt-1 block font-mono text-xs">{confirm}</code>
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              onClick={() => {
                runInTerminal(confirm);
                setConfirm(null);
                setFixed(true);
                onTerminal?.();
              }}
            >
              {t("notify.runIt")}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirm(null)}>
              {t("plugins.cancel")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
