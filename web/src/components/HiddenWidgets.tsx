// Today widgets a person hid (the X on a card), and the way back: `hz system ui hide|show <id> --slot today` is the one
// operation behind the X, Undo, Restore and the CLI, so every head shows the same list (ADR 0056).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { EyeOff, RotateCcw, Undo2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, unwrap } from "@/api/client";
import { AnchoredPopover } from "@/components/AnchoredPopover";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { t } from "@/i18n";

export type HiddenWidget = { id: string; title: string; module: string };

/** The widgets the person hid from Today (on by default, off now), and the calls that hide and bring them back. */
export function useWidgetVisibility() {
  const queries = useQueryClient();
  const list = useQuery({
    queryKey: ["ui", "list"],
    queryFn: () => unwrap(api.GET("/system/ui/list")),
    retry: false,
  });
  const hidden: HiddenWidget[] = (list.data ?? [])
    .filter((info) => info.kind === "widget" && info.slots.today === false && info.defaults.today !== false)
    .map(({ id, title, module }) => ({ id, title, module }));
  const set = async (ids: string[], shown: boolean) => {
    // One at a time: each call rewrites the same user file.
    for (const item of ids) {
      await unwrap(api.POST(shown ? "/system/ui/show" : "/system/ui/hide", { params: { query: { item, slot: "today" } } }));
    }
    await queries.invalidateQueries({ queryKey: ["ui"] });
  };
  return { hidden, hide: (id: string) => set([id], false), restore: (ids: string[]) => set(ids, true) };
}

/** "Hidden. Undo" right after hiding; it goes by itself. */
export function HideToast({ onUndo, onDone }: { onUndo: () => void; onDone: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onDone, 8000);
    return () => window.clearTimeout(timer);
  }, [onDone]);
  return (
    <div
      role="status"
      className="fixed bottom-16 left-1/2 z-(--layer-popover) flex -translate-x-1/2 items-center gap-2 rounded-xl border border-border bg-popover p-1.5 pl-3 text-sm shadow-xl"
    >
      <span>{t("today.hidden")}</span>
      <PaneActions className="mt-0 border-t-0 p-0">
        <PaneAction key="undo" icon={Undo2} onClick={onUndo}>
          {t("today.undo")}
        </PaneAction>
      </PaneActions>
    </div>
  );
}

/** The header control: only there while something is hidden; opens the list with a Restore per widget and Restore all. */
export function HiddenWidgets({
  hidden,
  restore,
}: {
  hidden: HiddenWidget[];
  restore: (ids: string[]) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => {
      if (event.target instanceof Node && !anchor.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  useEffect(() => {
    if (!hidden.length) setOpen(false);
  }, [hidden.length]);
  if (!hidden.length) return null;
  return (
    <span ref={anchor} className="relative ml-auto inline-flex">
      <PaneAction
        ghost
        icon={EyeOff}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen(!open)}
      >
        {t("today.hiddenCount", { count: hidden.length })}
      </PaneAction>
      <span
        aria-hidden
        className="pointer-events-none absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full bg-primary text-[10px] text-primary-foreground tabular-nums"
      >
        {hidden.length}
      </span>
      {open && (
        <AnchoredPopover anchor={anchor} role="dialog" aria-label={t("today.hiddenWidgets")} focusFirst className="w-72">
          <h2 className="px-1.5 py-1 text-sm font-medium">{t("today.hiddenWidgets")}</h2>
          <ul>
            {hidden.map((widget) => (
              <li key={widget.id} className="flex items-center gap-2 px-1.5 py-1 text-sm">
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{widget.title}</span>
                  <span className="block truncate text-xs text-muted-foreground">{widget.module}</span>
                </span>
                <div data-pane-chrome="" className="flex shrink-0 items-center">
                  <PaneAction ghost icon={Undo2} onClick={() => void restore([widget.id])}>
                    {t("today.restore", { title: widget.title })}
                  </PaneAction>
                </div>
              </li>
            ))}
          </ul>
          <PaneActions>
            <PaneAction key="all" icon={RotateCcw} onClick={() => void restore(hidden.map((widget) => widget.id))}>
              {t("today.restoreAll")}
            </PaneAction>
          </PaneActions>
        </AnchoredPopover>
      )}
    </span>
  );
}

/** The control for the Today page, which keeps its own list of hidden widgets in step with the cards. */
export function TodayHiddenWidgets() {
  const { hidden, restore } = useWidgetVisibility();
  return <HiddenWidgets hidden={hidden} restore={restore} />;
}
