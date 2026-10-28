// Installation uses the browser's prompt or platform-specific Safari guidance. Installed apps open from their icon.
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { install, useInstallState } from "./install";

export function InstallApp({ compact = false }: { compact?: boolean }) {
  const state = useInstallState();
  if (state === "installed") {
    return compact ? null : <p className="text-xs text-muted-foreground">{t("install.installed")}</p>;
  }
  if (state === "prompt") {
    return (
      <Button variant={compact ? "ghost" : "outline"} size="sm" onClick={() => void install()}>
        <Download aria-hidden />
        {t("install.action")}
      </Button>
    );
  }
  if (state === "safari") return <p className="text-xs text-muted-foreground">{t("install.safari")}</p>;
  if (state === "ios") return <p className="text-xs text-muted-foreground">{t("install.ios")}</p>;
  return compact ? null : <p className="text-xs text-muted-foreground">{t("install.unsupported")}</p>;
}
