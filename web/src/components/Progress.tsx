// The one progress bar of the app: a row of segments, one per part of the work (a step, a plugin), or a single bar for
// a plain count. Only what the head reported is drawn: a segment is waiting, running, done, needs a look, skipped or
// failed, never a made-up percentage. The native <progress> carries the role, value and text for assistive technology;
// the coloured bar is decoration, and the segment names are announced as a list. The tokens switch with the theme and
// the pulse only runs when the person has not asked for less motion.

import { type MessageKey, t } from "@/i18n";
import type { PhaseState } from "@/lib/operationRun";
import { cn } from "@/lib/utils";

export type ProgressSegment = { id: string; label: string; state: PhaseState };

const FILL: Record<PhaseState, string> = {
  pending: "bg-muted",
  running: "bg-primary",
  done: "bg-ok",
  warn: "bg-warn",
  skipped: "bg-muted",
  failed: "bg-bad",
};
const FINISHED: PhaseState[] = ["done", "warn", "skipped"];
const BAR = "h-1.5 flex-1 rounded-full";

type Props = Readonly<{
  /** What is progressing, for the accessible name ("Steps", "Update progress"). */
  label: string;
  className?: string;
}> &
  (
    | Readonly<{
        segments: ProgressSegment[];
        /** Replaces "2 of 3" as the spoken value. */
        valueText?: string;
        /** Announce each segment's name and state as a list; off where a visible list already does it. */
        names?: boolean;
        /** Pulse the running segment; off once the work has stopped. Default on. */
        pulse?: boolean;
      }>
    | Readonly<{
        value: number;
        max: number;
        segments?: undefined;
        valueText?: string;
        names?: undefined;
        pulse?: undefined;
      }>
  );

export function Progress(props: Props) {
  const { label, className, valueText } = props;
  if (props.segments === undefined) {
    const { value, max } = props;
    const share = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
    return (
      <div className={className}>
        <progress
          className="sr-only"
          aria-label={label}
          max={max}
          value={value}
          aria-valuetext={valueText ?? t("progress.of", { done: value, total: max })}
        />
        <div aria-hidden className={cn(BAR, "w-full flex-none overflow-hidden bg-muted")}>
          <div className="h-full rounded-full bg-primary" style={{ width: `${share}%` }} />
        </div>
      </div>
    );
  }
  const { segments, names = true, pulse = true } = props;
  if (!segments.length) return null;
  const finished = segments.filter((segment) => FINISHED.includes(segment.state)).length;
  return (
    <div className={className}>
      <progress
        className="sr-only"
        aria-label={label}
        max={segments.length}
        value={finished}
        aria-valuetext={valueText ?? t("progress.of", { done: finished, total: segments.length })}
      />
      <div aria-hidden className="flex gap-1">
        {segments.map((segment) => (
          <span
            key={segment.id}
            title={segment.label}
            className={cn(
              BAR,
              FILL[segment.state],
              segment.state === "running" && pulse && "motion-safe:animate-pulse",
            )}
          />
        ))}
      </div>
      {names && (
        <ul className="sr-only">
          {segments.map((segment) => (
            <li key={segment.id}>
              {segment.label}: {t(`op.state.${segment.state}` as MessageKey)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
