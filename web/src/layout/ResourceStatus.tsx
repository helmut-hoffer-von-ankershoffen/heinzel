import { RefreshCw } from "lucide-react";
import { Link } from "react-router";
import { useResources } from "@/api/resources";
import { FooterItem, type State } from "@/components/FooterItem";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

const percent = (value: number | null | undefined) => (value == null ? t("resource.unknown") : `${Math.round(value)}%`);
const number = (value: number | null | undefined) => (value == null ? t("resource.unknown") : value.toFixed(2));
const tone = (value: number | null | undefined): State =>
  value == null ? "off" : value >= 90 ? "bad" : value >= 75 ? "warn" : "ok";
const COLOUR: Record<State, string> = { off: "bg-muted-foreground", ok: "bg-ok", warn: "bg-warn", bad: "bg-bad" };

function Bar({ value, state }: { value: number | null | undefined; state: State }) {
  return (
    <span data-state={state} className="flex h-4 w-1 items-end overflow-hidden rounded-sm bg-muted-foreground/15">
      <span
        className={cn("block w-full", COLOUR[state], value == null && "opacity-30")}
        style={{ height: `${value == null ? 100 : Math.min(100, Math.max(8, value))}%` }}
      />
    </span>
  );
}

export function ResourceStatus() {
  const query = useResources();
  const report = query.isError ? undefined : query.data;
  const cpu = report?.cpu_percent;
  const memory = report?.memory_percent;
  const load = report?.load_1m;
  const cores = report?.logical_cores;
  const loadState: State =
    load == null || cores == null ? "off" : load > 2 * cores ? "bad" : load > cores ? "warn" : "ok";
  const states = [tone(cpu), tone(memory), loadState];
  const state: State = states.includes("bad")
    ? "bad"
    : states.every((item) => item === "off")
      ? "off"
      : states.includes("warn") || states.includes("off") || report?.problems.length
        ? "warn"
        : "ok";
  const summary = t("resource.summary", { cpu: percent(cpu), memory: percent(memory), load: number(load) });
  return (
    <FooterItem
      label={t("resource.title")}
      triggerLabel={t("resource.trigger", { summary })}
      state={state}
      content={
        <span aria-hidden className="flex h-4 items-end gap-px">
          <Bar value={cpu} state={tone(cpu)} />
          <Bar value={memory} state={tone(memory)} />
          <Bar value={load == null || cores == null ? null : (load / (2 * cores)) * 100} state={loadState} />
        </span>
      }
    >
      <div className="space-y-2 px-1.5 pb-1.5">
        <p className="text-xs text-muted-foreground">{t("resource.host")}</p>
        <dl className="grid grid-cols-2 gap-x-2 gap-y-1 tabular-nums">
          <dt>{t("resource.cpu")}</dt>
          <dd>{percent(cpu)}</dd>
          <dt>{t("resource.memory")}</dt>
          <dd>{percent(memory)}</dd>
          <dt>{t("resource.load")}</dt>
          <dd>{number(load)}</dd>
        </dl>
        <p className="text-xs text-muted-foreground">
          {t("resource.loadDetail", { cores: cores ?? t("resource.unknown") })}
        </p>
        <p className="text-xs text-muted-foreground">{t("resource.loadMeaning")}</p>
        {query.isPending && <p>{t("resource.loading")}</p>}
        {query.isError && <p role="status">{t("resource.failed")}</p>}
        {report && states.includes("off") && <p>{t("resource.partial")}</p>}
        {report?.problems.map((message) => (
          <p key={message} className="text-warn">
            {message}
          </p>
        ))}
        {report && (
          <p className="text-xs text-muted-foreground">
            {t("resource.sampled", { at: new Date(report.sampled_at).toLocaleString() })}
          </p>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Link to="/doctor" className="underline">
            {t("health.doctor")}
          </Link>
          <button
            type="button"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
            className="inline-flex min-h-6 items-center gap-1 rounded px-1 hover:bg-accent disabled:opacity-50"
          >
            <RefreshCw aria-hidden className="size-3" />
            {t("resource.refresh")}
          </button>
        </div>
      </div>
    </FooterItem>
  );
}
