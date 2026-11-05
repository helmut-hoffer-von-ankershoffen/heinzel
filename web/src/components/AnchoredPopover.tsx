// A dropdown that stays usable in the visible phone viewport, including inside scrolling dialogs and panels.
import { type ComponentPropsWithoutRef, type RefObject, useLayoutEffect, useRef, useState } from "react";
import { placePopover, popoverHeight } from "@/lib/placement";
import { cn } from "@/lib/utils";
import { watchViewport } from "@/lib/viewport";

type Props = Omit<ComponentPropsWithoutRef<"div">, "style"> & {
  anchor: RefObject<HTMLElement | null>;
  side?: "above" | "below";
  focusFirst?: boolean;
  contentRef?: RefObject<HTMLDivElement | null>;
};

export function AnchoredPopover({
  anchor,
  side = "below",
  focusFirst = false,
  contentRef,
  className,
  children,
  ...props
}: Props) {
  const pane = useRef<HTMLDivElement>(null);
  const [positioned, setPositioned] = useState(false);
  useLayoutEffect(() => {
    const update = () => {
      const target = anchor.current?.getBoundingClientRect();
      const box = pane.current?.getBoundingClientRect();
      if (!target || !box) return;
      const maxHeight = popoverHeight(target);
      const next = {
        ...placePopover(target, { width: box.width, height: Math.min(box.height, maxHeight) }, side),
        maxHeight,
      };
      const element = pane.current;
      if (!element) return;
      // Child queries can commit before ResizeObserver. Position at that mutation checkpoint, before a press ends.
      for (const key of ["left", "top", "maxHeight"] as const) {
        const value = `${next[key]}px`;
        if (element.style[key] !== value) element.style[key] = value;
      }
      setPositioned(true);
    };
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    if (pane.current) observer?.observe(pane.current);
    if (anchor.current) observer?.observe(anchor.current);
    const mutations = new MutationObserver((records) => {
      // Placement owns these styles; observing its own writes would schedule another layout measurement.
      if (records.some((record) => record.target !== pane.current || record.attributeName !== "style")) update();
    });
    if (pane.current)
      mutations.observe(pane.current, { subtree: true, childList: true, characterData: true, attributes: true });
    const unwatch = watchViewport(update);
    window.addEventListener("scroll", update, true);
    return () => {
      observer?.disconnect();
      mutations.disconnect();
      unwatch();
      window.removeEventListener("scroll", update, true);
    };
  }, [anchor, side]);
  useLayoutEffect(() => {
    if (positioned && focusFirst)
      pane.current?.querySelector<HTMLElement>("input,button,textarea,[tabindex]")?.focus({ preventScroll: true });
  }, [positioned, focusFirst]);
  return (
    <div
      {...props}
      ref={(element) => {
        pane.current = element;
        if (contentRef) contentRef.current = element;
      }}
      style={{ visibility: positioned ? undefined : "hidden" }}
      className={cn(
        "viewport-popover fixed z-(--layer-popover) overflow-y-auto rounded-xl border border-border bg-popover p-1.5 shadow-xl",
        className,
      )}
    >
      {children}
    </div>
  );
}
