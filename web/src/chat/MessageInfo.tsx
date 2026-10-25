// A tiny (i) on each answer; on hover or focus it shows when the answer was generated, how long it took, the AI access,
// model and effort, the tokens and the cost (ADR 0057). The cost is labelled honestly: a subscription CLI reports what
// the run would cost at API prices, which the plan covers; only API-key access is real spend.
import { Info } from "lucide-react";
import { useId, useState } from "react";
import type { SchemaChatMessage } from "@/api/client";
import { format, t } from "@/i18n";

const tokens = new Intl.NumberFormat("en");

/** The answer's cost as people should read it: plan usage at API prices, real spend, or what counted. */
export function costLabel(message: SchemaChatMessage): string {
  if (message.cost_usd == null) {
    return message.budget_usd != null
      ? t("chat.costCounted", { cap: format.usd(message.budget_usd) })
      : t("chat.costUnknown");
  }
  const cost = format.usd(message.cost_usd);
  return message.billing === "plan" ? t("chat.planCost", { cost }) : cost;
}

function seconds(value: number): string {
  return value < 10 ? `${value.toFixed(1)} s` : `${Math.round(value)} s`;
}

export function MessageInfo({ message, first }: { message: SchemaChatMessage; first: boolean }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const via = [
    message.access || message.runner || t("chat.agent"),
    message.model,
    message.effort && t("chat.effortIs", { effort: message.effort }),
  ]
    .filter(Boolean)
    .join(" · ");
  const counts = [
    message.input_tokens != null && t("chat.tokensIn", { count: tokens.format(message.input_tokens) }),
    message.output_tokens != null && t("chat.tokensOut", { count: tokens.format(message.output_tokens) }),
    message.cache_read_tokens ? t("chat.cacheRead", { count: tokens.format(message.cache_read_tokens) }) : null,
    message.cache_write_tokens ? t("chat.cacheWrite", { count: tokens.format(message.cache_write_tokens) }) : null,
  ].filter(Boolean);
  // A coding agent's CLI sends its system prompt and tool list with every request: say so once, on the first answer.
  const explain = first && message.access !== "pydantic-ai" && (message.runner || message.access);
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-label={t("chat.aboutAnswer")}
        aria-describedby={panelId}
        // A tooltip: shown while hovered or focused (a tap focuses it), hidden on Escape, never toggled by a click.
        onFocus={() => setOpen(true)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => event.key === "Escape" && setOpen(false)}
        className="inline-flex size-5 items-center justify-center rounded-full text-muted-foreground hover:text-foreground focus-visible:ring-1 focus-visible:ring-ring [&_svg]:size-3.5"
      >
        <Info aria-hidden />
      </button>
      <span
        id={panelId}
        role="tooltip"
        hidden={!open}
        className="absolute bottom-6 left-0 z-20 w-72 rounded-lg border border-border bg-popover p-3 text-xs text-foreground shadow-md"
      >
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          <dt className="text-muted-foreground">{t("chat.generated")}</dt>
          <dd>{format.dateTime(message.at)}</dd>
          {message.duration_seconds != null && (
            <>
              <dt className="text-muted-foreground">{t("chat.took")}</dt>
              <dd>{seconds(message.duration_seconds)}</dd>
            </>
          )}
          <dt className="text-muted-foreground">{t("chat.via")}</dt>
          <dd className="break-all">{via}</dd>
          {counts.length > 0 && (
            <>
              <dt className="text-muted-foreground">{t("chat.tokens")}</dt>
              <dd>{counts.join(" · ")}</dd>
            </>
          )}
          <dt className="text-muted-foreground">{t("chat.costLabel")}</dt>
          <dd>{costLabel(message)}</dd>
        </dl>
        {explain && <span className="mt-2 block text-muted-foreground">{t("chat.whyCost")}</span>}
      </span>
    </span>
  );
}
