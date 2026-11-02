// The footer's switch for automatic updates (ADR 0116): the same setting `hz.auto_update` as its row in Settings →
// Updates, read from and written to `hz system config`, so a change in one shows in the other at once. While hz runs
// for development both are disabled and say why instead (`development` from `GET /api/auto-update`).

import { useId } from "react";
import { useConfig, useSetSetting } from "@/api/queries";
import { t } from "@/i18n";
import { Switch } from "@/layout/SettingControls";
import { useAutoUpdateHeld } from "./autoUpdateApi";

/** The setting in effect: on unless someone turned it off (its default is on). */
export function useAutoUpdateSetting(): { on: boolean; known: boolean; fromEnv: boolean } {
  const config = useConfig();
  const setting = config.data?.settings.find((entry) => entry.section === "hz" && entry.key === "auto_update");
  const value = setting?.value ?? setting?.default;
  return { on: value !== false, known: setting !== undefined, fromEnv: setting?.source === "env" };
}

export function AutoUpdateToggle() {
  const { on, known, fromEnv } = useAutoUpdateSetting();
  const development = useAutoUpdateHeld();
  const save = useSetSetting();
  const labelId = useId();
  return (
    <div className="flex items-start gap-3 border-t border-border/70 pt-2">
      <div className="min-w-0 flex-1">
        <p id={labelId}>{t("autoUpdate.setting")}</p>
        <p className="text-xs text-muted-foreground">{development ?? t("autoUpdate.settingHelp")}</p>
        {save.error && (
          <p role="alert" className="text-xs text-bad">
            {save.error.message}
          </p>
        )}
      </div>
      <Switch
        labelledBy={labelId}
        on={on && development === null}
        disabled={!known || fromEnv || development !== null || save.isPending}
        onToggle={() => save.mutate({ key: "hz.auto_update", value: on ? "false" : "true" })}
      />
    </div>
  );
}
