// One item of the footer: only an icon, in the colour and shape of its state, or a short text; what it says in a pane
// above it on hover, focus or a click (which pins it), like the clock's. Every footer item is drawn with this, so
// plugin items get it for free: they declare `tone` as data and the renderer does the rest.
import type { LucideIcon } from "lucide-react";
import { type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { t } from "@/i18n";
import { placePopover, popoverHeight } from "@/lib/placement";
import { cn } from "@/lib/utils";
import { watchViewport } from "@/lib/viewport";
import { FOOTER_RELOCATED, useFooterVisibility } from "./FooterLayout";
import { PaneActions, PaneTitle } from "./PaneActions";

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
  paneName,
  hideHeading = false,
  steady = false,
  actions,
  freeform,
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
  /** The pane's accessible name, when it differs from `label` (a live value's time changes every second). */
  paneName?: string;
  /** No visible heading, when the trigger already shows the same words. */
  hideHeading?: boolean;
  /** `text` changes while shown (a clock): fixed-width digits and a width that never shrinks, so neighbours stay put. */
  steady?: boolean;
  /** Every action of the pane: `PaneAction`s in the one bottom row under a divider, after all content. */
  actions?: ReactNode;
  /**
   * Why this pane may not follow the pane structure (content, divider, one row of action buttons). Only the
   * version pane, whose release notes are prose with their own links; allowlisted in pane-actions-standards.test.ts.
   */
  freeform?: string;
}) {
  // Longest text seen, in characters: monospace `ch` units make the slot's width exact and never smaller.
  const widest = useRef(0);
  if (steady && text) widest.current = Math.max(widest.current, [...text].length);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const wrapper = useRef<HTMLSpanElement>(null);
  const pane = useRef<HTMLDivElement>(null);
  const [positioned, setPositioned] = useState(false);
  const position = useRef<(() => void) | null>(null);
  const returningFocus = useRef(false);
  const visible = useFooterVisibility();
  const id = useId();
  const open = visible && (hovered || focused || pinned);
  // In development and tests, an open pane that breaks the structure fails loudly, on each render and late change.
  const structured = useRef(!freeform);
  structured.current = !freeform;
  useEffect(() => {
    if (!visible) {
      setHovered(false);
      setFocused(false);
      setPinned(false);
    }
  }, [visible]);
  useLayoutEffect(() => {
    if (!open) {
      setPositioned(false);
      return;
    }
    const anchor = wrapper.current,
      element = pane.current;
    if (!anchor || !element) return;
    let active = true;
    const place = () => {
      if (!active) return;
      const box = element.getBoundingClientRect(),
        trigger = anchor.getBoundingClientRect();
      const maxHeight = popoverHeight(trigger);
      const target =
        align === "left"
          ? { left: trigger.left, right: trigger.left + box.width, top: trigger.top, bottom: trigger.bottom }
          : trigger;
      const next = {
        ...placePopover(target, { width: box.width, height: Math.min(box.height, maxHeight) }, "above"),
        maxHeight,
      };
      // A descendant query can commit without rerendering this footer item. Place at its mutation
      // checkpoint, before a physical press can finish against the child's new geometry.
      for (const key of ["left", "top", "maxHeight"] as const) {
        const value = `${next[key]}px`;
        if (element.style[key] !== value) element.style[key] = value;
      }
      setPositioned(true);
    };
    position.current = place;
    place();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place);
    observer?.observe(anchor);
    observer?.observe(element);
    const mutations = new MutationObserver((records) => {
      // Ignore placement's own writes; content changes still need synchronous measurement.
      if (records.some((record) => record.target !== element || record.attributeName !== "style")) {
        if (structured.current) assertPaneStructure(element);
        place();
      }
    });
    mutations.observe(element, { subtree: true, childList: true, characterData: true, attributes: true });
    const unwatchViewport = watchViewport(place);
    window.addEventListener(FOOTER_RELOCATED, place);
    window.addEventListener("scroll", place, true);
    return () => {
      active = false;
      position.current = null;
      observer?.disconnect();
      mutations.disconnect();
      unwatchViewport();
      window.removeEventListener(FOOTER_RELOCATED, place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, align]);
  useLayoutEffect(() => {
    if (open && structured.current && pane.current) assertPaneStructure(pane.current);
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
      className="relative inline-flex shrink-0"
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
          steady && "font-mono tabular-nums",
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
          <span
            className={steady ? "inline-block text-center" : undefined}
            style={steady ? { minWidth: `${widest.current}ch` } : undefined}
          >
            {text}
          </span>
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
            className={cn(
              "viewport-popover fixed overflow-y-auto rounded-xl",
              pinned ? "z-[calc(var(--layer-popover,40)+1)]" : "z-[var(--layer-popover,40)]",
            )}
            style={{ visibility: positioned ? undefined : "hidden" }}
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
              aria-label={paneName ?? label}
              className={cn(
                "w-72 max-w-full rounded-xl border border-border bg-popover p-2 text-sm text-foreground shadow-xl",
                popoverClassName,
              )}
            >
              {!hideHeading && <PaneTitle>{label}</PaneTitle>}
              {children}
              <PaneActions>{actions}</PaneActions>
            </section>
          </div>,
          document.body,
        )}
    </span>
  );
}

/** Buttons and links a pane may show only in its bottom action row; switches and form fields are content. */
const ACTION = 'button:not([role="switch"]), a[href], [role="button"], [role="link"], [role="menuitem"]';

/**
 * What breaks the pane structure (design/README.md, "Pane actions"): content first, then one divider and one
 * row of action buttons, nothing after it, and no button or link anywhere else in the pane.
 */
export function paneStructureProblems(pane: Element): string[] {
  const rows = pane.querySelectorAll("[data-pane-actions]");
  const problems: string[] = [];
  if (rows.length > 1) problems.push(`${rows.length} action rows; a pane has one`);
  const row = rows[0];
  for (const element of pane.querySelectorAll(ACTION))
    if (!row?.contains(element))
      problems.push(`"${element.getAttribute("aria-label") ?? element.textContent?.trim()}" is outside the action row`);
  const all = pane.querySelectorAll("*");
  const last = all[all.length - 1];
  if (row && last && !row.contains(last)) problems.push("content follows the action row");
  if (row) {
    const actions = [...row.querySelectorAll(ACTION)];
    const name = (element: Element) => element.getAttribute("aria-label") ?? element.textContent?.trim();
    for (const action of actions) {
      // Only PaneAction draws an action: icon-only by default, words beside the icon only with a stated reason.
      if (!action.hasAttribute("data-pane-action")) problems.push(`"${name(action)}" is not a PaneAction`);
      else if (!action.querySelector("svg")) problems.push(`"${name(action)}" has no icon`);
      else if (action.getAttribute("data-pane-action") === "icon" && !action.getAttribute("aria-label"))
        problems.push(`"${name(action)}" is icon-only without an accessible name`);
    }
    // One row, never wrapped: every action starts at the same height (measurable wherever layout runs).
    const tops = new Set(actions.map((action) => Math.round(action.getBoundingClientRect().top)));
    if (tops.size > 1) problems.push(`the actions wrap onto ${tops.size} lines; use icon-only actions`);
  }
  return problems;
}

/** Throws, in development and tests only, when the open pane breaks the structure. */
function assertPaneStructure(pane: Element) {
  if (!import.meta.env.DEV) return;
  const problems = paneStructureProblems(pane);
  if (problems.length) throw new Error(`Footer pane structure (design/README.md): ${problems.join("; ")}`);
}
