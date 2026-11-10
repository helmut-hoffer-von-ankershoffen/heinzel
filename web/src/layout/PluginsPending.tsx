import { useIsMutating, useQueryClient } from "@tanstack/react-query";
import { CircleStop, RefreshCw, RotateCw, X } from "lucide-react";
import { useState } from "react";
import { pluginChangeKey, usePluginsPending } from "@/api/queries";
import { FooterItem } from "@/components/FooterItem";
import { PaneAction, PaneActions } from "@/components/PaneActions";
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
  const ready = !activation.blocked && !busy && !activation.observation && !activation.pending;
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
      {confirming && ready && (
        <div role="alertdialog" aria-label={action} className="space-y-1">
          <p>{t("lifecycle.question", { target })}</p>
          <p className="text-muted-foreground">{t("lifecycle.pageStays")}</p>
        </div>
      )}
      {activation.phase && !activation.error && <p role="status">{t(`lifecycle.phase.${activation.phase}`)}</p>}
      {activation.error && (
        <p role="alert" className="text-bad">
          {activation.error}
        </p>
      )}
    </>
  );
  // Every button sits in the one bottom row under the divider, as in every footer pane.
  const row = confirming && ready ? ({ align: "end", words: "all" } as const) : undefined;
  const actions = (
    <>
      {ready && !activation.changing && !confirming && (
        <PaneAction icon={RotateCw} primary onClick={() => setConfirming(true)}>
          {t("lifecycle.action")}
        </PaneAction>
      )}
      {/* The inline confirmation answers like a dialog: right-aligned, words on both, Cancel first. */}
      {confirming && ready && (
        <>
          <PaneAction icon={X} onClick={() => setConfirming(false)}>
            {t("lifecycle.cancel")}
          </PaneAction>
          <PaneAction icon={RotateCw} primary onClick={start}>
            {action}
          </PaneAction>
        </>
      )}
      {activation.error && activation.observation && !activation.pending && !activation.changing && (
        <PaneAction icon={RefreshCw} onClick={start}>
          {t("lifecycle.checkAgain")}
        </PaneAction>
      )}
      {activation.pending && (activation.phase === "reconnecting" || activation.phase === "verifying") && (
        <PaneAction icon={CircleStop} onClick={stopPluginActivation}>
          {t("lifecycle.stopChecking")}
        </PaneAction>
      )}
    </>
  );
  if (inline) {
    return (
      <section className="mb-4 space-y-2 rounded-xl border border-warn/50 bg-warn/5 p-3 text-sm">
        <h3 className="font-medium">{t("pluginsPending.restartRequired")}</h3>
        {details}
        <PaneActions className="px-0" {...row}>
          {actions}
        </PaneActions>
      </section>
    );
  }
  return (
    <FooterItem
      label={t("pluginsPending.title")}
      state="warn"
      icon={RotateCw}
      className="animate-pulse text-warn"
      actions={actions}
      actionRow={row}
    >
      <div className="space-y-2 px-1.5 pb-1.5">{details}</div>
    </FooterItem>
  );
}
