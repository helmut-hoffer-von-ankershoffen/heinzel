// "Install app": the browser's own install prompt on Chrome and Edge, where Safari keeps it (File > Add to Dock), or
// nothing when the app is installed already. Installed, it opens from the Dock in a window of its own.
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
  return compact ? null : <p className="text-xs text-muted-foreground">{t("install.unsupported")}</p>;
}
