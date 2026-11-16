// Settings > Browser: where links open in the desktop app. The choice lives in the desktop app, not in this page.
import { useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { linkModeAvailable, readLinkMode, writeLinkMode } from "./linkMode";

export function BrowserSettings() {
  const id = useId();
  const available = linkModeAvailable();
  const [on, setOn] = useState<boolean | null>(null);
  const browser = (
    window as Window & {
      heinzelDesktop?: { browser?: (request: Record<string, unknown>) => Promise<{ notice?: string | null }> };
    }
  ).heinzelDesktop?.browser;
  const [clearing, setClearing] = useState(false);
  const [clearStatus, setClearStatus] = useState<"done" | "failed" | null>(null);
  const clear = async () => {
    if (!browser || clearing) return;
    setClearing(true);
    setClearStatus(null);
    try {
      const result = await browser({ action: "clear" });
      setClearStatus(result.notice === "clear-failed" ? "failed" : "done");
    } catch {
      setClearStatus("failed");
    } finally {
      setClearing(false);
    }
  };
  useEffect(() => {
    if (available) void readLinkMode().then(setOn, () => setOn(true));
  }, [available]);
  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-lg font-medium">{t("settings.browserTitle")}</h2>
      {available ? (
        <div className="flex flex-col gap-1">
          <label htmlFor={id} className="flex items-center gap-2 text-sm">
            <input
              id={id}
              type="checkbox"
              role="switch"
              aria-checked={on === true}
              checked={on === true}
              disabled={on === null}
              onChange={(event) => void writeLinkMode(event.target.checked).then(setOn, () => {})}
            />
            {t("settings.linksInBrowser")}
          </label>
          <p className="text-xs text-muted-foreground">{t("settings.linksInBrowserHelp")}</p>
        </div>
      ) : !browser ? (
        <p className="text-sm text-muted-foreground">{t("settings.browserWebOnly")}</p>
      ) : null}
      {browser && (
        <div className="flex flex-col items-start gap-2 text-sm">
          <p className="text-muted-foreground">{t("browser.private")}</p>
          <p className="text-muted-foreground">{t("browser.automationNote")}</p>
          <p className="text-muted-foreground">{t("browser.clearDataHint")}</p>
          <Button type="button" variant="outline" disabled={clearing} onClick={() => void clear()}>
            {t("browser.clearData")}
          </Button>
          {clearStatus && (
            <p role={clearStatus === "failed" ? "alert" : "status"}>
              {t(clearStatus === "failed" ? "browser.clearFailedNote" : "browser.clearDone")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
