// One item of the footer: only an icon, in the colour and shape of its state, or a short text; what it says in a pane
// above it on hover, focus or a click (which pins it), like the clock's. Every footer item is drawn with this, so
// plugin items get it for free: they declare `tone` as data and the renderer does the rest.
import { CircleCheck, CircleX, type LucideIcon, Minus, TriangleAlert } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

export type State = "ok" | "warn" | "bad" | "off";

/** A declared tone as a state: anything not marked good or needing attention is off (grey). */
export const stateOf = (tone: "neutral" | "ok" | "warn" | "bad" | undefined): State =>
  tone === "ok" || tone === "warn" || tone === "bad" ? tone : "off";

const COLOUR: Record<State, string> = {
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  off: "text-muted-foreground",
};
/** The shape says the state too, so colour is never the only signal: tick, triangle, cross, dash. */
const SHAPE: Record<State, LucideIcon> = { ok: CircleCheck, warn: TriangleAlert, bad: CircleX, off: Minus };
const WORD: Record<State, string> = {
  ok: "state.ok",
  warn: "state.warn",
  bad: "state.bad",
  off: "state.off",
};
const CLOSE_DELAY = 200;

export function FooterItem({
  label,
  state,
  icon: Icon,
  text,
  children,
  align = "right",
}: {
  /** What it says, as its accessible name and the pane's heading. */
  label: string;
  state: State;
  /** Its icon; without one, `text` is shown. */
  icon?: LucideIcon;
  text?: string;
  /** The pane's body: detail lines, links, buttons. */
  children?: ReactNode;
  align?: "left" | "right";
}) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const wrapper = useRef<HTMLSpanElement>(null);
  const id = useId();
  const open = hovered || focused || pinned;
  const Shape = SHAPE[state];
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (!pinned) return;
    const outside = (event: MouseEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setPinned(false);
    };
    window.addEventListener("mousedown", outside);
    return () => window.removeEventListener("mousedown", outside);
  }, [pinned]);
  const close = () => {
    window.clearTimeout(timer.current);
    setHovered(false);
    setFocused(false);
    setPinned(false);
  };
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: hover and focus only show what the button opens on click
    <span
      ref={wrapper}
      className="relative shrink-0"
      data-state={state}
      onMouseEnter={() => {
        window.clearTimeout(timer.current);
        setHovered(true);
      }}
      onMouseLeave={() => {
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setHovered(false), CLOSE_DELAY);
      }}
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        if (!wrapper.current?.contains(event.relatedTarget as Node | null)) setFocused(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation();
          close();
        }
      }}
    >
      <button
        type="button"
        onClick={() => setPinned((was) => !was)}
        aria-expanded={open}
        aria-label={Icon ? undefined : label}
        aria-controls={open ? id : undefined}
        className={cn(
          "relative flex h-6 min-w-6 items-center justify-center gap-1 rounded px-1 hover:bg-accent/60",
          Icon ? COLOUR[state] : "text-ui-sm hover:text-foreground",
        )}
      >
        {Icon ? (
          <>
            <Icon aria-hidden className="size-4" />
            <Shape aria-hidden className="absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full bg-background" />
            <span className="sr-only">
              {label}, {t(WORD[state] as "state.ok")}
            </span>
          </>
        ) : (
          <span>{text}</span>
        )}
      </button>
      {open && (
        // The gap is padding inside this transparent box, so the pointer never leaves it on the way up.
        <div
          id={id}
          className={cn("absolute bottom-full z-(--layer-popover) pb-2", align === "right" ? "right-0" : "left-0")}
        >
          <section
            aria-label={label}
            className="w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-border bg-popover p-2 text-sm text-foreground shadow-xl"
          >
            <p className="flex items-center gap-2 px-1.5 py-1 font-medium">
              {Icon && <Shape aria-hidden className={cn("size-3.5 shrink-0", COLOUR[state])} />}
              <span className="min-w-0">{label}</span>
            </p>
            {children}
          </section>
        </div>
      )}
    </span>
  );
}
