// One item of the footer: only an icon, in the colour and shape of its state, or a short text; what it says in a pane
// above it on hover, focus or a click (which pins it), like the clock's. Every footer item is drawn with this, so
// plugin items get it for free: they declare `tone` as data and the renderer does the rest.
import { Circle, CircleCheck, CircleX, type LucideIcon, TriangleAlert } from "lucide-react";
import { type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { placePopover } from "@/sound/dock";
import { FOOTER_RELOCATED, useFooterVisibility } from "./FooterLayout";

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
/** A small state mark for the detail pane; footer triggers use only their own icon. */
const SHAPE: Record<State, LucideIcon> = { ok: CircleCheck, warn: TriangleAlert, bad: CircleX, off: Circle };
const WORD: Record<State, string> = {
  ok: "state.ok",
  warn: "state.warn",
  bad: "state.bad",
  off: "state.off",
};
const CLOSE_DELAY = 200;

export function FooterItem({
  label,
  triggerLabel,
  onActivate,
  disabled = false,
  state,
  icon: Icon,
  text,
  content,
  children,
  align = "right",
  className,
  popoverClassName,
}: {
  /** What it says, as its accessible name and the pane's heading. */
  label: string;
  /** A custom trigger's visible words, when they differ from the pane heading. */
  triggerLabel?: string;
  /** A deliberate one-click action; hover/focus still inspect its detail without running it. */
  onActivate?: () => void;
  disabled?: boolean;
  state: State;
  /** Its icon; without one, `text` is shown. */
  icon?: LucideIcon;
  text?: string;
  /** A compact custom trigger; the button keeps its accessible label. */
  content?: ReactNode;
  /** The pane's body: detail lines, links, buttons. */
  children?: ReactNode;
  align?: "left" | "right";
  /** Extra trigger styling for a specific status, such as pulsing when an update is ready. */
  className?: string;
  /** Optional pane width for content that needs more room than the standard compact footer popover. */
  popoverClassName?: string;
}) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const wrapper = useRef<HTMLSpanElement>(null);
  const pane = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<{ left: number; top: number; maxHeight: number } | null>(null);
  const position = useRef<(() => void) | null>(null);
  const returningFocus = useRef(false);
  const visible = useFooterVisibility();
  const id = useId();
  const open = visible && (hovered || focused || pinned);
  const Shape = SHAPE[state];
  useEffect(() => {
    if (!visible) {
      setHovered(false);
      setFocused(false);
      setPinned(false);
    }
  }, [visible]);
  useLayoutEffect(() => {
    if (!open) {
      setAt(null);
      return;
    }
    const anchor = wrapper.current,
      element = pane.current;
    if (!anchor || !element) return;
    const place = () => {
      const box = element.getBoundingClientRect(),
        trigger = anchor.getBoundingClientRect();
      const maxHeight = Math.max(1, window.innerHeight - 16);
      const target =
        align === "left"
          ? { left: trigger.left, right: trigger.left + box.width, top: trigger.top, bottom: trigger.bottom }
          : trigger;
      const next = {
        ...placePopover(target, { width: box.width, height: Math.min(box.height, maxHeight) }, "above"),
        maxHeight,
      };
      setAt((current) =>
        current?.left === next.left && current.top === next.top && current.maxHeight === next.maxHeight
          ? current
          : next,
      );
    };
    position.current = place;
    place();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place);
    observer?.observe(anchor);
    observer?.observe(element);
    window.addEventListener("resize", place);
    window.addEventListener(FOOTER_RELOCATED, place);
    window.addEventListener("scroll", place, true);
    return () => {
      position.current = null;
      observer?.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener(FOOTER_RELOCATED, place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, align]);
  useLayoutEffect(() => {
    if (open) position.current?.();
  });
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (!pinned) return;
    const outside = (event: MouseEvent) => {
      if (!wrapper.current?.contains(event.target as Node) && !pane.current?.contains(event.target as Node))
        setPinned(false);
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
      onFocus={() => {
        if (!returningFocus.current) setFocused(true);
      }}
      onBlur={(event) => {
        if (
          !wrapper.current?.contains(event.relatedTarget as Node | null) &&
          !pane.current?.contains(event.relatedTarget as Node | null)
        )
          setFocused(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation();
          close();
          returningFocus.current = true;
          wrapper.current?.querySelector("button")?.focus();
          returningFocus.current = false;
        }
      }}
    >
      <button
        type="button"
        onClick={onActivate ?? (() => setPinned((was) => !was))}
        disabled={disabled}
        aria-expanded={onActivate ? undefined : open}
        aria-label={
          Icon ? undefined : content === undefined ? label : `${triggerLabel ?? label}, ${t(WORD[state] as "state.ok")}`
        }
        aria-controls={open ? id : undefined}
        className={cn(
          "relative flex h-6 min-w-6 items-center justify-center gap-1 rounded px-1 hover:bg-accent/60 disabled:cursor-wait disabled:opacity-60",
          Icon ? COLOUR[state] : "text-ui-sm hover:text-foreground",
          className,
        )}
      >
        {Icon ? (
          <>
            <Icon aria-hidden className="size-4" />
            <span className="sr-only">
              {label}, {t(WORD[state] as "state.ok")}
            </span>
          </>
        ) : content === undefined ? (
          <span>{text}</span>
        ) : (
          content
        )}
      </button>
      {open &&
        createPortal(
          // The pane shares hover/focus with its trigger across the small gap, and stays above body-portaled media.
          // biome-ignore lint/a11y/noStaticElementInteractions: hover only previews what the trigger opens on click
          <div
            ref={pane}
            id={id}
            className="fixed z-[var(--layer-popover,40)] overflow-y-auto rounded-xl"
            style={at ?? { left: 0, top: 0, visibility: "hidden" }}
            onMouseEnter={() => {
              window.clearTimeout(timer.current);
              setHovered(true);
            }}
            onMouseLeave={() => {
              window.clearTimeout(timer.current);
              timer.current = window.setTimeout(() => setHovered(false), CLOSE_DELAY);
            }}
            onFocus={() => setFocused(true)}
          >
            <section
              aria-label={label}
              className={cn(
                "w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-border bg-popover p-2 text-sm text-foreground shadow-xl",
                popoverClassName,
              )}
            >
              <p className="flex items-center gap-2 px-1.5 py-1 font-medium">
                {Icon && <Shape aria-hidden className={cn("size-3.5 shrink-0", COLOUR[state])} />}
                <span className="min-w-0">{label}</span>
              </p>
              {children}
            </section>
          </div>,
          document.body,
        )}
    </span>
  );
}
