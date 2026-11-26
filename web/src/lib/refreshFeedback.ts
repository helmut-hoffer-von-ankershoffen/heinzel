import { t } from "@/i18n";
import { cn } from "@/lib/utils";

/** A refresh in flight supersedes its previous failure; a successful read clears it. */
export function refreshIcon(busy: boolean, failed: boolean) {
  return cn(busy && "motion-safe:animate-spin", !busy && failed && "text-bad");
}

export function refreshName(label: string, busy: boolean, failed: boolean) {
  return !busy && failed ? `${label} — ${t("top.refreshFailed")}` : label;
}
