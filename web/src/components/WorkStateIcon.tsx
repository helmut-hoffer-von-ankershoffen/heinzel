import { BriefcaseBusiness, CircleHelp, Moon, PartyPopper } from "lucide-react";
import { t } from "@/i18n";
import type { WorkingHours } from "@/lib/workingHours";

const ICONS = {
  working: BriefcaseBusiness,
  "off-duty": PartyPopper,
  sleeping: Moon,
  unavailable: CircleHelp,
} as const;

/** A compact, accessible indication of the local work state; the exact schedule stays in the tooltip. */
export function WorkStateIcon({ work }: { work: WorkingHours }) {
  const Icon = ICONS[work.state];
  const label = t(`work.state.${work.state}`);
  return (
    <span
      role="img"
      aria-label={label}
      title={[label, work.summary, work.detail]
        .filter((part, index, parts) => parts.indexOf(part) === index)
        .join(" · ")}
      className="inline-flex shrink-0 text-muted-foreground"
    >
      <Icon aria-hidden className="size-4" />
    </span>
  );
}
