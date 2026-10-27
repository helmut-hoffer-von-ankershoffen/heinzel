// The stream's mini-player card, docked or floating (see dock.ts). The card always lives in document.body, so docking
// and popping out never move it in the DOM: a moved iframe reloads and the stream would restart. Docked it is laid
// over the sidebar's dock slot (its position and width follow the slot, and the slot keeps the card's height, so
// nothing overlaps); floating it is a card over the content that can be dragged, moved with the arrow keys, and
// snapped back by letting go near the dock spot. In the collapsed rail the docked card is hidden, with playback
// controlled by the rail button; the same iframe remains mounted when the full sidebar returns.
import { ExternalLink, GripHorizontal, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
import { type KeyboardEvent, type PointerEvent, type ReactNode, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { t } from "@/i18n";
import { clampToWindow } from "@/lib/placement";
import { cn } from "@/lib/utils";
import { watchViewport } from "@/lib/viewport";
import { nearSlot, setDocked, setFloating, useDockPref, useDockSlot } from "./dock";
import { stop } from "./store";

const WIDTH = 288;
const HEADER = 44;
/** Movement under this many pixels is a click, not a drag. */
const DRAG_PX = 6;
const STEP = 16;

const iconButton = "flex size-7 shrink-0 items-center justify-center rounded-lg text-icon hover:bg-accent";

type Props = { title: string; watchUrl: string; host: string; children: ReactNode };

export function PlayerCard({ title, watchUrl, host, children }: Props) {
  const pref = useDockPref();
  const slot = useDockSlot();
  const docked = pref.docked && slot !== null;
  const compact = docked && slot?.dataset.soundCompact === "true";
  const card = useRef<HTMLElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number; moved: boolean } | null>(null);
  // While dragging: how far the card is from where it began.
  const [offset, setOffset] = useState<{ dx: number; dy: number } | null>(null);
  // Docked: where the dock slot is now; the card is laid over it.
  const [at, setAt] = useState<{ left: number; top: number; width: number } | null>(null);

  useLayoutEffect(() => {
    if (!docked || !slot) {
      setAt(null);
      return;
    }
    const measure = () => {
      const rect = slot.getBoundingClientRect();
      setAt((was) =>
        was && was.left === rect.left && was.top === rect.top && was.width === rect.width
          ? was
          : { left: rect.left, top: rect.top, width: rect.width },
      );
    };
    measure();
    const watch = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    watch?.observe(slot);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    document.addEventListener("transitionend", measure, true);
    return () => {
      watch?.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
      document.removeEventListener("transitionend", measure, true);
    };
  }, [docked, slot]);

  // Docked, the slot keeps the card's height, so what follows it in the sidebar stays below the card.
  useLayoutEffect(() => {
    const element = card.current;
    if (!docked || compact || !slot || !element) return;
    const reserve = () => {
      slot.style.height = `${element.offsetHeight}px`;
      // Resizing the slot can move its top as the sidebar's flex navigation gives up space. Read again now,
      // before paint, so the portal card follows the reserved box on its first docked frame.
      const rect = slot.getBoundingClientRect();
      setAt((was) =>
        was && was.left === rect.left && was.top === rect.top && was.width === rect.width
          ? was
          : { left: rect.left, top: rect.top, width: rect.width },
      );
    };
    reserve();
    const watch = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(reserve);
    watch?.observe(element);
    return () => {
      watch?.disconnect();
      slot.style.height = "";
    };
  }, [docked, compact, slot]);

  useLayoutEffect(() => {
    if (pref.docked) return;
    const keepVisible = () => {
      const rect = card.current?.getBoundingClientRect();
      if (!rect) return;
      const next = clampToWindow(rect.left, rect.top, rect);
      if (next.x !== rect.left || next.y !== rect.top) setFloating(next.x, next.y);
    };
    keepVisible();
    return watchViewport(keepVisible);
  }, [pref.docked]);

  const size = () => {
    const rect = card.current?.getBoundingClientRect();
    return { width: rect?.width || WIDTH, height: rect?.height || HEADER };
  };
  const floatAt = (x: number, y: number) => {
    const at = clampToWindow(x, y, size());
    setFloating(at.x, at.y);
  };

  const down = (event: PointerEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest("a,button")) return;
    const rect = card.current?.getBoundingClientRect();
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      left: rect?.left ?? 0,
      top: rect?.top ?? 0,
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const move = (event: PointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = event.clientX - d.x;
    const dy = event.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) < DRAG_PX) return;
    d.moved = true;
    setOffset({ dx, dy });
  };
  const up = (event: PointerEvent<HTMLElement>) => {
    const d = drag.current;
    drag.current = null;
    setOffset(null);
    if (!d?.moved) return;
    const x = d.left + event.clientX - d.x;
    const y = d.top + event.clientY - d.y;
    const spot = slot?.getBoundingClientRect();
    if (!docked) {
      if (spot && nearSlot(x, y, spot)) setDocked(true);
      else floatAt(x, y);
    } else floatAt(x, y); // pulled out of the sidebar: it floats where it was dropped
  };
  const key = (event: KeyboardEvent<HTMLButtonElement>) => {
    const step = event.shiftKey ? STEP * 4 : STEP;
    const delta: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const by = delta[event.key];
    if (!by) return;
    event.preventDefault();
    const rect = card.current?.getBoundingClientRect();
    floatAt((pref.x ?? rect?.left ?? 0) + by[0], (pref.y ?? rect?.top ?? 0) + by[1]);
  };

  const floatingStyle = pref.x === null || pref.y === null ? { left: 12, bottom: 68 } : { left: pref.x, top: pref.y };
  const dockedAt = at ?? { left: 0, top: 0, width: WIDTH };
  const style = docked
    ? { left: dockedAt.left + (offset?.dx ?? 0), top: dockedAt.top + (offset?.dy ?? 0), width: dockedAt.width }
    : offset
      ? { left: (pref.x ?? drag.current?.left ?? 0) + offset.dx, top: (pref.y ?? drag.current?.top ?? 0) + offset.dy }
      : floatingStyle;

  const body = (
    <section
      ref={card}
      aria-label={t("sound.player", { title })}
      data-dock={docked ? "docked" : "floating"}
      style={style}
      hidden={compact}
      className={cn(
        "viewport-popover fixed z-(--layer-media) overflow-y-auto bg-popover",
        docked
          ? "border-y border-border"
          : "w-72 max-w-[calc(var(--hz-viewport-width,100vw)-1rem)] rounded-xl border border-border shadow-xl",
      )}
    >
      <header
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={() => {
          drag.current = null;
          setOffset(null);
        }}
        className="flex cursor-grab touch-none items-center gap-1 px-3 py-1.5 active:cursor-grabbing"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-ui-sm font-medium">{title}</p>
          <p className="truncate text-xs text-muted-foreground">{t("sound.playsFrom", { host })}</p>
        </div>
        {!docked && (
          <button
            type="button"
            onKeyDown={key}
            aria-label={t("sound.move")}
            title={t("sound.move")}
            className={iconButton}
          >
            <GripHorizontal aria-hidden className="size-3.5" />
          </button>
        )}
        {docked ? (
          <button
            type="button"
            onClick={() => {
              const rect = card.current?.getBoundingClientRect();
              if (!rect) return setDocked(false);
              const at = clampToWindow(rect.right + 16, rect.top, size());
              setFloating(at.x, at.y);
            }}
            aria-label={t("sound.popOut")}
            title={t("sound.popOut")}
            className={iconButton}
          >
            <PanelLeftOpen aria-hidden className="size-3.5" />
          </button>
        ) : (
          slot && (
            <button
              type="button"
              onClick={() => setDocked(true)}
              aria-label={t("sound.dock")}
              title={t("sound.dock")}
              className={iconButton}
            >
              <PanelLeftClose aria-hidden className="size-3.5" />
            </button>
          )
        )}
        <a
          href={watchUrl}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t("sound.openOn", { host })}
          title={t("sound.openOn", { host })}
          className={iconButton}
        >
          <ExternalLink aria-hidden className="size-3.5" />
        </a>
        <button
          type="button"
          onClick={stop}
          aria-label={t("sound.close")}
          title={t("sound.close")}
          className={iconButton}
        >
          <X aria-hidden className="size-3.5" />
        </button>
      </header>
      {children}
    </section>
  );
  return createPortal(body, document.body);
}
