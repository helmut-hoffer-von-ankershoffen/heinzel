import { useQueryClient } from "@tanstack/react-query";
import { t } from "@/i18n";
import type { ApplyOptions } from "@/lifecycle/controller";
import { RestartAndApply } from "@/lifecycle/RestartAndApply";

/** Restart only the authenticated server serving this window, and keep its open work mounted. */
export function WebServerSettings({ options }: { options?: ApplyOptions }) {
  const client = useQueryClient();
  return (
    <section aria-label={t("webServer.title")} className="space-y-4 text-sm">
      <h3 className="text-ui font-medium">{t("webServer.title")}</h3>
      <p className="text-muted-foreground">{t("webServer.description")}</p>
      <RestartAndApply
        target={t("webServer.title")}
        changes={[t("webServer.disconnect")]}
        actionLabel={t("webServer.restart")}
        confirmation={t("webServer.confirmation")}
        appliedMessage={t("webServer.reconnected")}
        options={options}
        onApplied={() => client.invalidateQueries()}
      />
    </section>
  );
}
