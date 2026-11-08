// The footer calendar glyph: a 2px draining ring with the minutes inside it ("Ledger", owner pick 7 Nov 2026).
// Fixed 24px footprint; tabular numerals so a changing number never moves a neighbour. Colour never carries a fact
// alone: free time is dashed, the next meeting is a shape, and the number says the rest.

import { cn } from "@/lib/utils";
import { type GlyphModel, labelFontSize } from "./calendarModel";

const R = 10.5;
const point = (radius: number, angle: number): [number, number] => {
  const t = ((angle - 90) * Math.PI) / 180;
  return [12 + radius * Math.cos(t), 12 + radius * Math.sin(t)];
};
const f = (n: number) => Math.round(n * 100) / 100;

/** A clockwise arc on the ring from `from` to `to` degrees. */
function arc(from: number, to: number): string {
  if (to - from >= 359.9) return `M12 ${f(12 - R)}A${R} ${R} 0 1 1 12 ${f(12 + R)}A${R} ${R} 0 1 1 12 ${f(12 - R)}`;
  if (to <= from) return "";
  const [x0, y0] = point(R, from);
  const [x1, y1] = point(R, to);
  return `M${f(x0)} ${f(y0)}A${R} ${R} 0 ${to - from > 180 ? 1 : 0} 1 ${f(x1)} ${f(y1)}`;
}

const LEVEL_CLASS = { calm: "text-foreground", warn: "text-warn", bad: "text-bad" } as const;

function Calendar({ scale }: { scale: number }) {
  return (
    <g
      transform={`translate(12 12) scale(${scale}) translate(-12 -12)`}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M8 2v4M16 2v4M3 10h18" />
      <rect x="3" y="4" width="18" height="18" rx="2" />
    </g>
  );
}

export function CalendarGlyph({ model, className }: { model: GlyphModel; className?: string }) {
  const base = cn("size-6 shrink-0 overflow-visible", className);
  if (model.kind === "unknown") {
    return (
      <svg aria-hidden viewBox="0 0 24 24" className={cn(base, "text-muted-foreground")} data-glyph="unknown">
        <Calendar scale={0.6} />
        <circle cx="18.5" cy="18.5" r="5" className="fill-popover" stroke="currentColor" strokeWidth="1" />
        <text x="18.5" y="21" textAnchor="middle" fontSize="7.5" fontWeight="700" fill="currentColor">
          ?
        </text>
      </svg>
    );
  }
  if (model.kind === "idle") {
    return (
      <svg aria-hidden viewBox="0 0 24 24" className={cn(base, "text-foreground")} data-glyph="idle">
        <circle cx="12" cy="12" r={R} fill="none" stroke="currentColor" strokeWidth="2" className="opacity-20" />
        <Calendar scale={0.55} />
      </svg>
    );
  }
  const width = model.level === "warn" ? 3 : 2;
  const meeting = model.kind === "meeting";
  const from = meeting ? 360 * model.fraction : 360 * (1 - model.fraction);
  const colour = meeting || model.level !== "calm" ? LEVEL_CLASS[model.level] : "text-muted-foreground";
  const marker = model.marker;
  const size = labelFontSize(model.text);
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={cn(base, LEVEL_CLASS[model.level], model.level === "bad" && "motion-safe:animate-pulse")}
      data-glyph={model.kind}
      data-level={model.level}
    >
      <circle cx="12" cy="12" r={R} fill="none" stroke="currentColor" strokeWidth="2" className="opacity-20" />
      <path
        d={arc(from, 360)}
        fill="none"
        strokeWidth={width}
        stroke="currentColor"
        strokeDasharray={meeting ? undefined : `${f(width * 0.9)} ${f(width * 0.9)}`}
        className={colour}
        data-ring
      />
      {marker?.kind === "diamond" &&
        (() => {
          const [x, y] = point(R, marker.angle);
          return (
            <rect
              data-marker="diamond"
              data-red={marker.red}
              x={f(x - 2.3)}
              y={f(y - 2.3)}
              width="4.6"
              height="4.6"
              transform={`rotate(45 ${f(x)} ${f(y)})`}
              strokeWidth="1.6"
              className={marker.red ? "fill-bad stroke-bad" : "fill-popover stroke-current"}
            />
          );
        })()}
      {marker?.kind === "stroke" &&
        (() => {
          const [x0, y0] = point(R + 1.8, marker.angle);
          const [x1, y1] = point(R - 2.4, marker.angle);
          return (
            <path
              data-marker={marker.gap === 0 ? "back-to-back" : "gap"}
              data-angle={f(marker.angle)}
              d={`M${f(x0)} ${f(y0)}L${f(x1)} ${f(y1)}`}
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
            />
          );
        })()}
      {marker?.kind === "dot" && (
        <circle
          data-marker="soon"
          cx="12"
          cy={f(12 - R)}
          r="2"
          className="fill-popover"
          stroke="currentColor"
          strokeWidth="1.4"
        />
      )}
      {model.level === "bad" && <circle cx="12" cy="12" r={f(R - width - 0.8)} fill="currentColor" />}
      <text
        x="12"
        y={f(12 + size * 0.36)}
        textAnchor="middle"
        fontSize={size}
        fontWeight="700"
        className={model.level === "bad" ? "fill-popover" : "fill-current"}
        style={{ fontVariantNumeric: "tabular-nums", letterSpacing: "-0.02em" }}
        data-label
      >
        {model.text}
      </text>
    </svg>
  );
}
