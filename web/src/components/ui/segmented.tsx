// A choice among two to four exclusive options (design/README.md, Choices): a radiogroup of radio segments on a muted
// track. The chosen segment is the one filled primary in its container; a choice the app cannot offer stays in the
// group, dimmed and aria-disabled, with its reason as the tooltip. Roving tabindex: Tab enters at the chosen segment,
// arrows move between segments.
import type { LucideIcon } from "lucide-react";
import { Loader2 } from "lucide-react";
import { type KeyboardEvent, useId, useLayoutEffect, useRef } from "react";
import { cn } from "@/lib/utils";

export type Segment<T extends string> = {
  readonly value: T;
  readonly label: string;
  readonly icon: LucideIcon;
  /** Why it cannot be chosen here; the segment stays reachable so the reason is read. */
  readonly unavailable?: string;
};

type Props<T extends string> = {
  readonly legend: string;
  readonly segments: readonly Segment<T>[];
  readonly value: T | null;
  readonly onSelect: (value: T) => void;
  /** Arrows select as they move (a cheap choice); off when choosing asks the browser for a permission. */
  readonly selectOnArrow?: boolean;
  /** The segment being asked for: the selected look with a spinner, the group busy. */
  readonly busy?: T | null;
};

export function SegmentedControl<T extends string>({
  legend,
  segments,
  value,
  onSelect,
  selectOnArrow = false,
  busy = null,
}: Props<T>) {
  const legendId = useId();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const focused = useRef(false);
  const shown = busy ?? value;
  const focusable = Math.max(
    0,
    segments.findIndex((segment) => segment.value === shown),
  );
  // A segment the app stops offering can leave while it has focus; keep the focus in the group, on the chosen one.
  const offered = segments.map((segment) => segment.value).join();
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs when the offered segments change
  useLayoutEffect(() => {
    if (focused.current && (document.activeElement === document.body || document.activeElement === null))
      buttons.current[focusable]?.focus();
  }, [offered]);
  const move = (event: KeyboardEvent, index: number) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const next = (index + step + segments.length) % segments.length;
    buttons.current[next]?.focus();
    const segment = segments[next];
    if (selectOnArrow && segment && !segment.unavailable) onSelect(segment.value);
  };
  return (
    <div className="space-y-1">
      <p id={legendId} className="text-xs text-muted-foreground">
        {legend}
      </p>
      <div
        role="radiogroup"
        aria-labelledby={legendId}
        aria-busy={busy ? true : undefined}
        onFocus={() => {
          focused.current = true;
        }}
        onBlur={(event) => {
          focused.current = event.currentTarget.contains(event.relatedTarget as Node | null);
        }}
        className="flex gap-0.5 rounded-lg border border-border bg-muted p-0.5"
      >
        {segments.map((segment, index) => {
          const checked = segment.value === shown;
          const Icon = busy === segment.value ? Loader2 : segment.icon;
          return (
            // biome-ignore lint/a11y/useSemanticElements: a segmented control, announced as the radios of its group
            <button
              key={segment.value}
              ref={(element) => {
                buttons.current[index] = element;
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-disabled={segment.unavailable || (busy && !checked) ? true : undefined}
              title={segment.unavailable}
              tabIndex={index === focusable ? 0 : -1}
              onKeyDown={(event) => move(event, index)}
              onClick={() => {
                if (busy) return;
                onSelect(segment.value);
              }}
              className={cn(
                "flex min-h-11 min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-md px-1 text-xs",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                checked
                  ? "bg-primary font-medium text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
                segment.unavailable && "opacity-50",
              )}
            >
              <Icon aria-hidden className={cn("size-4", busy === segment.value && "animate-spin")} />
              <span className="max-w-full truncate">{segment.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
