import { type KeyboardEvent, type PointerEvent, useEffect, useRef } from "react";
import { t } from "@/i18n";
import { SIDEBAR_DEFAULT, SIDEBAR_MIN, SIDEBAR_STEP, SIDEBAR_STEP_LARGE } from "@/lib/sidebarWidth";

/**
 * The draggable divider on the right sidebar's left edge: a separator with a wide hit area and a 1 px line.
 * `onChange(width, final)` gets live widths while dragging (`final` false) and the settled one to remember (`final` true).
 */
export function SidebarResizer({
  width,
  max,
  onChange,
}: {
  width: number;
  max: number;
  onChange: (width: number, final: boolean) => void;
}) {
  const drag = useRef<{ x: number; width: number; id: number } | null>(null);
  const frame = useRef(0);
  const latest = useRef(width);
  latest.current = width;
  useEffect(
    () => () => {
      cancelAnimationFrame(frame.current);
      delete document.documentElement.dataset.sidebarResizing;
    },
    [],
  );

  const move = (event: PointerEvent<HTMLDivElement>) => {
    const start = drag.current;
    if (!start || event.pointerId !== start.id) return;
    const next = start.width + (start.x - event.clientX);
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => onChange(next, false));
    latest.current = next;
  };
  const end = (event: PointerEvent<HTMLDivElement>, keep: boolean) => {
    const start = drag.current;
    if (!start || event.pointerId !== start.id) return;
    drag.current = null;
    cancelAnimationFrame(frame.current);
    delete document.documentElement.dataset.sidebarResizing;
    if (event.currentTarget.hasPointerCapture(start.id)) event.currentTarget.releasePointerCapture(start.id);
    onChange(keep ? latest.current : start.width, true);
  };
  const key = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? SIDEBAR_STEP_LARGE : SIDEBAR_STEP;
    const next =
      event.key === "ArrowLeft"
        ? width + step
        : event.key === "ArrowRight"
          ? width - step
          : event.key === "Home"
            ? SIDEBAR_MIN
            : event.key === "End"
              ? max
              : null;
    if (next === null) return;
    event.preventDefault();
    onChange(next, true);
  };

  return (
    // biome-ignore lint/a11y/useSemanticElements: a window splitter is a focusable separator, not an <hr>.
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={t("workspace.resize")}
      aria-valuenow={Math.round(width)}
      aria-valuemin={SIDEBAR_MIN}
      aria-valuemax={Math.round(max)}
      tabIndex={0}
      data-sidebar-resizer=""
      onKeyDown={key}
      onDoubleClick={() => onChange(SIDEBAR_DEFAULT, true)}
      onPointerDown={(event) => {
        if (event.pointerType === "mouse" && event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { x: event.clientX, width, id: event.pointerId };
        latest.current = width;
        document.documentElement.dataset.sidebarResizing = "";
      }}
      onPointerMove={move}
      onPointerUp={(event) => end(event, true)}
      onPointerCancel={(event) => end(event, false)}
      onLostPointerCapture={(event) => end(event, true)}
      className="group absolute inset-y-0 -left-1.5 z-20 w-3 cursor-col-resize touch-none outline-none [-webkit-app-region:no-drag]"
    >
      <span
        aria-hidden="true"
        className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover:bg-primary/60 group-focus-visible:bg-primary group-focus-visible:shadow-[0_0_0_2px_var(--color-primary)] motion-reduce:transition-none"
      />
    </div>
  );
}
