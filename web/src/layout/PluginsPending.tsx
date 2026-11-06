import { useIsMutating, useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useState } from "react";
import { pluginChangeKey, usePluginsPending } from "@/api/queries";
import { FooterItem } from "@/components/FooterItem";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import {
  activatePlugins,
  pluginInstallReceiptExists,
  stopPluginActivation,
  usePluginActivation,
} from "./pluginActivation";

/**
 * Installed plugins differ from what this server loaded, however they changed (`hz system plugin add` in a terminal,
 * another window): the footer and Settings list the changes and offer Restart and apply.
 * The control only appears while something is pending.
 */
export function PluginsPending({
  inline = false,
  reload = () => window.location.reload(),
}: {
  inline?: boolean;
  reload?: () => void;
}) {
  const pending = usePluginsPending();
  const activation = usePluginActivation();
  const client = useQueryClient();
  const changing = useIsMutating({ mutationKey: pluginChangeKey }) > 0;
  const [confirming, setConfirming] = useState(false);
  const changes = pending.data?.changes ?? [];
  if (changes.length === 0 && !activation.pending && !activation.observation && !activation.blocked) return null;
  const target = t("plugins.title");
  const action = t("lifecycle.targetAction", { target });
  const receipt = pluginInstallReceiptExists();
  const busy = receipt || changing || activation.changing;
  const start = () => {
    setConfirming(false);
    void activatePlugins(
      reload,
      () => !pluginInstallReceiptExists() && !client.isMutating({ mutationKey: pluginChangeKey }),
    );
  };
  const lines = changes.map((change) =>
    t(`pluginsPending.${change.change}`, { name: change.distribution, version: change.version ?? "" }),
  );
  const themes = changes.flatMap((change) => change.themes ?? []);
  const details = (
    <>
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
      {busy && !activation.pending && !activation.observation && (
        <p className="text-muted-foreground">{t("pluginsPending.finishChange")}</p>
      )}
      {!activation.blocked && !busy && !activation.observation && !activation.pending && !activation.changing && (
        <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
          {t("lifecycle.action")}
        </Button>
      )}
      {confirming && !activation.blocked && !busy && !activation.pending && !activation.observation && (
        <div role="alertdialog" aria-label={action} className="space-y-3 rounded-xl border border-border p-3">
          <p>{t("lifecycle.question", { target })}</p>
          <p className="text-muted-foreground">{t("lifecycle.pageStays")}</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={start}>
              {action}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              {t("lifecycle.cancel")}
            </Button>
          </div>
        </div>
      )}
      {activation.phase && !activation.error && <p role="status">{t(`lifecycle.phase.${activation.phase}`)}</p>}
      {activation.error && (
        <p role="alert" className="text-bad">
          {activation.error}
        </p>
      )}
      {activation.error && activation.observation && !activation.pending && !activation.changing && (
        <Button size="sm" variant="outline" onClick={start}>
          {t("lifecycle.checkAgain")}
        </Button>
      )}
      {activation.pending && (activation.phase === "reconnecting" || activation.phase === "verifying") && (
        <Button size="sm" variant="ghost" onClick={stopPluginActivation}>
          {t("lifecycle.stopChecking")}
        </Button>
      )}
    </>
  );
  if (inline) {
    return (
      <section className="mb-4 space-y-2 rounded-xl border border-warn/50 bg-warn/5 p-3 text-sm">
        <h3 className="font-medium">{t("pluginsPending.restartRequired")}</h3>
        {details}
      </section>
    );
  }
  return (
    <FooterItem label={t("pluginsPending.title")} state="warn" icon={RotateCw} className="animate-pulse text-warn">
      <div className="space-y-2 px-1.5 pb-1.5">{details}</div>
    </FooterItem>
  );
}
