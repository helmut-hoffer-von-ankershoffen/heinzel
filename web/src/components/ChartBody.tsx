// A chart a module describes as data (ChartWidget): a line, optional bars (0 to 1), a range band and a now marker,
// drawn as one static SVG in the app's colour tokens (light and dark follow the tokens). Nothing animates, so
// prefers-reduced-motion needs nothing. It is an image with the title as its name and `summary` as its description.
import { useId } from "react";
import type { components } from "@/api/schema";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

export type ChartData = components["schemas"]["ChartWidget"];

const W = 240;
const H = 96;
const PAD = { left: 4, right: 4, top: 6, bottom: 4 };
const LINE: Record<ChartData["tone"], string> = {
  neutral: "stroke-primary text-primary",
  ok: "stroke-ok text-ok",
  warn: "stroke-warn text-warn",
  bad: "stroke-bad text-bad",
};

/** A short axis label: the clock time of an ISO timestamp, else the label itself. */
export function axisLabel(at: string): string {
  const match = /^\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2})/.exec(at);
  return match ? (match[1] as string) : at;
}

const round = (n: number) => Math.round(n * 10) / 10;

export function ChartBody({ widget }: Readonly<{ widget: ChartData }>) {
  const id = useId();
  const { series, bars, range, now } = widget;
  const values = series.map((point) => point.value);
  const low = Math.min(...values, range?.low ?? Infinity);
  const high = Math.max(...values, range?.high ?? -Infinity);
  const span = high - low || 1;
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (innerW * i) / (series.length - 1);
  const y = (value: number) => PAD.top + innerH * (1 - (value - low) / span);
  const points = series.map((point, i) => `${round(x(i))},${round(y(point.value))}`).join(" ");
  const barWidth = Math.max(2, (innerW / series.length) * 0.6);
  const nowIndex = now === undefined || now === null ? -1 : series.findIndex((point) => point.at === now);
  const ticks = [...new Set([0, Math.floor((series.length - 1) / 2), series.length - 1])];
  const fmt = (value: number) => `${round(value)}`;
  const unit = widget.unit ? ` ${widget.unit}` : "";
  const extent = `${t("contrib.chart.span", { low: fmt(low), high: fmt(high) })}${unit}`;
  return (
    <figure className="flex flex-col gap-1">
      <h3 className="text-xs text-muted-foreground">{widget.title}</h3>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-labelledby={`${id}-t`}
        aria-describedby={`${id}-d`}
        className={cn("w-full", LINE[widget.tone])}
      >
        <title id={`${id}-t`}>{widget.title}</title>
        <desc id={`${id}-d`}>{widget.summary}</desc>
        {range && (
          <rect
            x={PAD.left}
            y={round(y(range.high))}
            width={innerW}
            height={round(Math.max(1, y(range.low) - y(range.high)))}
            className="fill-current opacity-10"
          />
        )}
        {range && (
          <g className="stroke-muted-foreground" strokeWidth={1} strokeDasharray="2 2">
            <line x1={PAD.left} x2={W - PAD.right} y1={round(y(range.high))} y2={round(y(range.high))} />
            <line x1={PAD.left} x2={W - PAD.right} y1={round(y(range.low))} y2={round(y(range.low))} />
          </g>
        )}
        <line x1={PAD.left} x2={W - PAD.right} y1={H - PAD.bottom} y2={H - PAD.bottom} className="stroke-border" />
        {bars?.map((bar, i) => (
          <rect
            key={series[i]?.at ?? i}
            x={round(x(i) - barWidth / 2)}
            y={round(H - PAD.bottom - bar * innerH * 0.5)}
            width={round(barWidth)}
            height={round(bar * innerH * 0.5)}
            className="fill-muted-foreground opacity-70"
          />
        ))}
        <polyline points={points} fill="none" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {nowIndex >= 0 && (
          <line
            x1={round(x(nowIndex))}
            x2={round(x(nowIndex))}
            y1={PAD.top}
            y2={H - PAD.bottom}
            strokeWidth={1}
            strokeDasharray="3 2"
            className="stroke-foreground"
          />
        )}
      </svg>
      <div aria-hidden className="flex justify-between text-xs tabular-nums text-muted-foreground">
        {ticks.map((i) => (
          <span key={i}>{axisLabel((series[i] as { at: string }).at)}</span>
        ))}
      </div>
      <table className="sr-only">
        <caption>{widget.title}</caption>
        <thead>
          <tr>
            <th scope="col">{t("contrib.chart.time")}</th>
            <th scope="col">{widget.unit || t("contrib.chart.value")}</th>
            {bars && <th scope="col">{widget.bars_label || t("contrib.chart.share")}</th>}
          </tr>
        </thead>
        <tbody>
          {series.map((point, i) => (
            <tr key={point.at}>
              <th scope="row">{axisLabel(point.at)}</th>
              <td>{fmt(point.value)}</td>
              {bars && <td>{Math.round((bars[i] ?? 0) * 100)}%</td>}
            </tr>
          ))}
        </tbody>
      </table>
      <figcaption className="text-xs tabular-nums text-muted-foreground">
        {[extent, widget.bars_label].filter(Boolean).join(" · ")}
      </figcaption>
    </figure>
  );
}
