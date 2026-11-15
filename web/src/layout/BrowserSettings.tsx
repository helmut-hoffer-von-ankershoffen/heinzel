// Settings > Browser: where links open in the desktop app. The choice lives in the desktop app, not in this page.
import { useEffect, useId, useState } from "react";
import { t } from "@/i18n";
import { linkModeAvailable, readLinkMode, writeLinkMode } from "./linkMode";

export function BrowserSettings() {
  const id = useId();
  const available = linkModeAvailable();
  const [on, setOn] = useState<boolean | null>(null);
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
      ) : (
        <p className="text-sm text-muted-foreground">{t("settings.browserWebOnly")}</p>
      )}
    </div>
  );
}
