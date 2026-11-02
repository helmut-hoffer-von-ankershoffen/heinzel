import { RotateCw } from "lucide-react";
import { usePluginsPending } from "@/api/queries";
import { FooterItem } from "@/components/FooterItem";
import { t } from "@/i18n";
import { RestartAndApply } from "@/lifecycle/RestartAndApply";

/**
 * Installed plugins differ from what this server loaded, however they changed (`hz system plugin add` in a terminal,
 * another window): the footer says which plugin and offers Restart and apply, so nobody has to find the Settings page
 * of an in-app install. It only appears while something is pending.
 */
export function PluginsPending({ reload = () => window.location.reload() }: { reload?: () => void }) {
  const pending = usePluginsPending();
  const changes = pending.data?.changes ?? [];
  if (changes.length === 0) return null;
  const lines = changes.map((change) =>
    t(`pluginsPending.${change.change}`, { name: change.distribution, version: change.version ?? "" }),
  );
  const themes = changes.flatMap((change) => change.themes ?? []);
  return (
    <FooterItem label={t("pluginsPending.title")} state="warn" icon={RotateCw} className="animate-pulse text-warn">
      <div className="space-y-2 px-1.5 pb-1.5">
        <p>{t("pluginsPending.intro")}</p>
        <ul className="list-inside list-disc">
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {themes.map((theme) => (
          <p key={theme} className="text-xs text-muted-foreground">
            {t("pluginsPending.theme", { theme })}
          </p>
        ))}
        <RestartAndApply target={t("plugins.title")} changes={lines} onApplied={() => reload()} />
      </div>
    </FooterItem>
  );
}
