// What "Update all" would change, shown in the operation dialog before the person agrees: hz itself, each plugin
// with where it goes or why it stays, and whether the desktop app comes along (the same plan as
// `hz system update --preview`).
import { useId, useState } from "react";
import type { SchemaPluginPlan, SchemaUpdatePlan } from "@/api/client";
import { type MessageKey, t } from "@/i18n";
import { Switch } from "@/layout/SettingControls";

/** One plugin's line: its name and what happens to it, in the words of the command line's plan. */
function pluginLine(plugin: SchemaPluginPlan): string {
  const installed = plugin.installed ?? t("updates.plan.unknownVersion");
  return t(`updates.plan.${plugin.action}` as MessageKey, {
    name: plugin.distribution,
    installed,
    target: plugin.target ?? "",
  });
}

/** Heinzel's own line, in the app's words: where it goes, that it is the newest, or that nobody could tell. */
function coreLine(plan: SchemaUpdatePlan): string {
  if (!plan.latest) return t("updates.plan.core.unknown", { current: plan.current });
  return plan.latest === plan.current
    ? t("updates.plan.core.current", { current: plan.current })
    : t("updates.plan.core.update", { current: plan.current, latest: plan.latest });
}

export function UpdatePlanDetails({
  plan,
  onDesktop,
}: Readonly<{ plan: SchemaUpdatePlan; onDesktop: (on: boolean) => void }>) {
  const [desktop, setDesktop] = useState(plan.desktop === true);
  const labelId = useId();
  return (
    <div className="mt-2 space-y-2 text-sm">
      <p>{coreLine(plan)}</p>
      {(plan.plugins ?? []).length > 0 && (
        <ul aria-label={t("updates.plan.plugins")} className="space-y-1">
          {(plan.plugins ?? []).map((plugin) => (
            <li key={plugin.distribution}>
              {pluginLine(plugin)}
              {plugin.detail && <span className="block text-xs text-muted-foreground">{plugin.detail}</span>}
            </li>
          ))}
        </ul>
      )}
      {plan.desktop && (
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <p id={labelId}>{t("updates.plan.desktop")}</p>
            <p className="text-xs text-muted-foreground">{t("updates.plan.desktopHelp")}</p>
          </div>
          <Switch
            labelledBy={labelId}
            on={desktop}
            disabled={false}
            onToggle={() => {
              setDesktop(!desktop);
              onDesktop(!desktop);
            }}
          />
        </div>
      )}
    </div>
  );
}
