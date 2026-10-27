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
  const [at, setAt] = useState<{ left: number; top: number; maxHeight: number } | null>(null);
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
      setAt((old) =>
        old?.left === next.left && old.top === next.top && old.maxHeight === next.maxHeight ? old : next,
      );
    };
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    if (pane.current) observer?.observe(pane.current);
    if (anchor.current) observer?.observe(anchor.current);
    const unwatch = watchViewport(update);
    window.addEventListener("scroll", update, true);
    return () => {
      observer?.disconnect();
      unwatch();
      window.removeEventListener("scroll", update, true);
    };
  }, [anchor, side]);
  const positioned = at !== null;
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
      style={at ?? { visibility: "hidden" }}
      className={cn(
        "viewport-popover fixed z-(--layer-popover) overflow-y-auto rounded-xl border border-border bg-popover p-1.5 shadow-xl",
        className,
      )}
    >
      {children}
    </div>
  );
}
