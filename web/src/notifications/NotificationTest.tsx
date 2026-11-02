// Test notifications on the device running this browser. Only desktop loopback pages also offer the server's native
// notifier and daemon repairs; a remote phone must never mistake a Mac's notification settings for its own.
import { BellRing, Check, RotateCcw, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api, type SchemaChannel } from "@/api/client";
import { useOpenNotificationSettings, useTestNotice } from "@/api/queries";
import { runCommand } from "@/components/CommandActions";
import { OperationDialog } from "@/components/OperationDialog";
import { Button } from "@/components/ui/button";
import { type MessageKey, t } from "@/i18n";
import { isLocalServer } from "@/lib/browser";
import { appCallFor } from "@/lib/operationCalls";
import { requestOperation } from "@/lib/operationRun";
import { cn } from "@/lib/utils";
import { type DesktopState, setDesktop, useDesktopState } from "./desktop";
import { popUp } from "./useNoticeStream";

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
  install: "notify.browser.install",
};

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
  const local = isLocalServer();
  const navigate = useNavigate();
  const [confirm, setConfirm] = useState<string | null>(null);
  const [fixed, setFixed] = useState(false);
  const [delivery, setDelivery] = useState<"idle" | "showing" | "shown" | "failed">("idle");
  const result = test.data;
  const again = () => {
    setFixed(false);
    setDelivery("idle");
    test.mutate(undefined, {
      onSuccess: async (result) => {
        if (browser !== "on") return;
        setDelivery("showing");
        const shown = await popUp(result.notice, navigate);
        setDelivery(shown ? "shown" : "failed");
        if (shown) void api.POST("/system/notify/ack", { params: { query: { ids: result.notice.id } } });
      },
    });
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
    const here = channel.command ? appCallFor(channel.command) : undefined;
    if (here) {
      return (
        <Button size="sm" variant="outline" onClick={fix(() => requestOperation(here))}>
          {t(channel.fix === "install-daemon" ? "notify.fix.installDaemon" : "notify.fix.turnOn")}
        </Button>
      );
    }
    if (channel.command) {
      const label = channel.fix === "install-notifier" ? t("notify.fix.installNotifier") : t("notify.fix.turnOn");
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
        { key: "web", label: t("notify.channel.web"), ok: true, detail: t("notify.app.open") },
        ...(local ? (result.channels ?? []).filter((channel) => channel.name !== "web") : []).map((channel) => ({
          key: channel.name,
          label: t(LABELS[channel.name]),
          ok: channel.state === "shown" || channel.state === "ready",
          detail: `${t(`notify.state.channel.${channel.state}` as MessageKey)}: ${channel.detail}`,
          action: channelAction(channel),
        })),
        {
          key: "browser",
          label: t("notify.channel.browser"),
          ok: browser === "on" && delivery === "shown",
          detail: t(delivery === "failed" ? "notify.browser.failed" : BROWSER[browser]),
          action:
            browser === "off" ? (
              <Button size="sm" variant="outline" onClick={fix(() => void setDesktop(true))}>
                {t("notify.fix.allowBrowser")}
              </Button>
            ) : null,
        },
      ]
    : [];
  const outside = delivery === "shown" || (local && result?.native === "shown");
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <OperationDialog />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={again} disabled={test.isPending || delivery === "showing"}>
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
            {outside ? t("notify.result.shown") : local ? t("notify.result.insideOnly") : t("notify.result.inApp")}
          </p>
          <ul className="flex flex-col gap-1.5">
            {rows.map((row) => (
              <li key={row.key} className="flex flex-wrap items-start gap-2">
                {row.ok ? (
                  <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-ok" />
                ) : (
                  <X aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
                )}
                <span className="min-w-0 flex-1 basis-40">
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
                runCommand(confirm);
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
