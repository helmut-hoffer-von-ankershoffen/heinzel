// Each control has one stable portal host; moving into More never creates another operation consumer.
import { Ellipsis } from "lucide-react";
import {
  type ComponentPropsWithoutRef,
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { t } from "@/i18n";
import { placePopover, popoverHeight } from "@/lib/placement";
import { cn } from "@/lib/utils";
import { watchViewport } from "@/lib/viewport";
import { fitFooter, footerWidths } from "./footerFit";

export const FOOTER_MORE = "@more";
export const FOOTER_CREDIT = "@credit";
export const FOOTER_RELOCATED = "hz:footer-relocated";
type Registered = { element: HTMLElement; host: HTMLElement; priority: number; pinned: boolean };
type Layout = { hidden: string[]; empty: string[]; compactCredit: boolean; width: number };
type Context = Layout & {
  register: (id: string, item: Registered) => () => void;
  overflowOpen: boolean;
  setOverflowOpen: (open: boolean) => void;
  overflowTarget: HTMLElement | null;
  setOverflowTarget: (element: HTMLElement | null) => void;
};
const FooterContext = createContext<Context | null>(null);
const FooterVisibility = createContext(true);
export const useFooterVisibility = () => useContext(FooterVisibility);
const targets = (element: HTMLElement) => [element, ...element.querySelectorAll("[data-footer-credit-text]")];
export function useFooterLayout() {
  const value = useContext(FooterContext);
  if (!value) throw new Error("Footer controls need FooterLayout");
  return value;
}

export function FooterLayout({ children, className, ...props }: ComponentPropsWithoutRef<"footer">) {
  const root = useRef<HTMLElement>(null);
  const registered = useRef(new Map<string, Registered>());
  const observer = useRef<ResizeObserver | null>(null);
  const changes = useRef<MutationObserver | null>(null);
  const pending = useRef<number | null>(null);
  const measurement = useRef(() => {});
  const restoreFocus = useRef<string | null>(null);
  const lastFocused = useRef<string | null>(null);
  const [overflowOpen, setOverflowOpen] = useState(false);
  const [overflowTarget, setOverflowTarget] = useState<HTMLElement | null>(null);
  const [layout, setLayout] = useState<Layout>({ hidden: [], empty: [], compactCredit: false, width: 0 });
  const queue = useCallback(() => {
    if (pending.current !== null) return;
    pending.current = requestAnimationFrame(() => {
      pending.current = null;
      measurement.current();
    });
  }, []);
  const register = useCallback(
    (id: string, item: Registered) => {
      registered.current.set(id, item);
      for (const element of targets(item.element)) observer.current?.observe(element);
      changes.current?.observe(item.element, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["data-state"],
      });
      queue();
      return () => {
        for (const element of targets(item.element)) observer.current?.unobserve(element);
        registered.current.delete(id);
        queue();
      };
    },
    [queue],
  );
  measurement.current = () => {
    const element = root.current;
    if (!element) return;
    const style = getComputedStyle(element);
    const available =
      element.getBoundingClientRect().width -
      (Number.parseFloat(style.paddingLeft) || 0) -
      (Number.parseFloat(style.paddingRight) || 0) -
      (Number.parseFloat(style.borderLeftWidth) || 0) -
      (Number.parseFloat(style.borderRightWidth) || 0);
    if (available <= 0) return;
    const gap = Number.parseFloat(style.columnGap) || 0;
    // Anchors retain source order when late providers mount or live controls move into the body portal.
    const measured = [...element.querySelectorAll<HTMLElement>("[data-footer-part]")].flatMap((anchor) => {
      const id = anchor.dataset.footerPart;
      const item = id ? registered.current.get(id) : null;
      if (!id || id === FOOTER_MORE || !item) return [];
      const state = item.element.querySelector<HTMLElement>("[data-state]")?.dataset.state;
      return [
        {
          id,
          width: item.element.getBoundingClientRect().width,
          pinned: item.pinned,
          priority: item.priority + (state === "bad" ? 200 : state === "warn" ? 100 : 0),
        },
      ];
    });
    const credit = registered.current.get(FOOTER_CREDIT)?.element;
    const textWidth = credit?.querySelector("[data-footer-credit-text]")?.getBoundingClientRect().width ?? 0;
    const width = credit?.getBoundingClientRect().width ?? 0;
    const variants = footerWidths(
      available,
      measured,
      gap,
      credit
        ? {
            id: FOOTER_CREDIT,
            expanded: width + (layout.compactCredit ? textWidth : 0),
            compact: 0,
          }
        : null,
    );
    const moreWidth = registered.current.get(FOOTER_MORE)?.element.getBoundingClientRect().width ?? 24;
    const next = fitFooter(available, variants.items, gap, moreWidth);
    const empty = variants.items.filter((item) => item.width <= 0).map((item) => item.id);
    const concealedFocus = next.hidden.find((id) =>
      registered.current.get(id)?.element.contains(document.activeElement),
    );
    if (concealedFocus && !overflowOpen) {
      lastFocused.current = concealedFocus;
      restoreFocus.current = FOOTER_MORE;
    } else if (!next.hidden.length && registered.current.get(FOOTER_MORE)?.element.contains(document.activeElement)) {
      restoreFocus.current =
        lastFocused.current && next.visible.includes(lastFocused.current)
          ? lastFocused.current
          : (next.visible.find((id) => id !== FOOTER_CREDIT) ?? null);
    }
    setLayout((current) =>
      current.width === available &&
      current.compactCredit === variants.compactCredit &&
      current.hidden.join("\0") === next.hidden.join("\0") &&
      current.empty.join("\0") === empty.join("\0")
        ? current
        : { hidden: next.hidden, empty, compactCredit: variants.compactCredit, width: available },
    );
  };
  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    observer.current = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(queue);
    changes.current = new MutationObserver(queue);
    changes.current.observe(element, { subtree: true, childList: true });
    observer.current?.observe(element);
    for (const item of registered.current.values()) {
      for (const target of targets(item.element)) observer.current?.observe(target);
      changes.current.observe(item.element, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["data-state"],
      });
    }
    const relocated = () => measurement.current();
    window.addEventListener(FOOTER_RELOCATED, relocated);
    window.addEventListener("resize", queue);
    document.fonts?.addEventListener("loadingdone", queue);
    queue();
    return () => {
      observer.current?.disconnect();
      observer.current = null;
      changes.current?.disconnect();
      changes.current = null;
      window.removeEventListener(FOOTER_RELOCATED, relocated);
      window.removeEventListener("resize", queue);
      document.fonts?.removeEventListener("loadingdone", queue);
      if (pending.current !== null) cancelAnimationFrame(pending.current);
      pending.current = null;
    };
  }, [queue]);
  useLayoutEffect(() => {
    const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (overflowTarget && overflowOpen) {
      layout.hidden.forEach((id, index) => {
        const host = registered.current.get(id)?.host;
        if (host && host.parentElement === overflowTarget && overflowTarget.children[index] !== host)
          overflowTarget.insertBefore(host, overflowTarget.children[index] ?? null);
      });
      if (focused && overflowTarget.contains(focused) && document.activeElement !== focused)
        focused.focus({ preventScroll: true });
    }
    const target = restoreFocus.current;
    if (target && (target === FOOTER_MORE ? layout.hidden.length > 0 : !layout.hidden.includes(target))) {
      registered.current.get(target)?.element.querySelector<HTMLElement>("button,a,input")?.focus();
      restoreFocus.current = null;
    }
  }, [layout.hidden, overflowTarget, overflowOpen]);
  return (
    <FooterContext value={{ ...layout, register, overflowOpen, setOverflowOpen, overflowTarget, setOverflowTarget }}>
      <footer
        {...props}
        data-footer-width={layout.width}
        ref={root}
        className={cn("relative flex min-w-0 items-center", className)}
      >
        {children}
      </footer>
    </FooterContext>
  );
}

export function FooterPart({
  id,
  priority = 0,
  pinned = false,
  label,
  children,
  className,
}: {
  id: string;
  priority?: number;
  pinned?: boolean;
  label?: string;
  children: ReactNode;
  className?: string;
}) {
  const anchor = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [host] = useState(() => document.createElement("div"));
  const { register, hidden, empty, overflowOpen, overflowTarget } = useFooterLayout();
  const concealed = id === FOOTER_MORE ? hidden.length === 0 : hidden.includes(id) || empty.includes(id);
  const inOverflow = hidden.includes(id) && overflowOpen && !!overflowTarget;
  useLayoutEffect(() => {
    if (!content.current) return;
    host.dataset.footerHost = id;
    return register(id, { element: content.current, host, priority, pinned });
  }, [id, priority, pinned, register, host]);
  useLayoutEffect(() => {
    const parent = inOverflow ? overflowTarget : anchor.current;
    if (!parent || host.parentElement === parent) return;
    const focused = host.contains(document.activeElement) ? (document.activeElement as HTMLElement) : null;
    parent.appendChild(host);
    host.className = inOverflow ? "w-full" : "inline-flex";
    if (focused && (!concealed || inOverflow)) focused.focus({ preventScroll: true });
    // Clamped panes recalculate when their anchor relocates without changing size.
    const frame = requestAnimationFrame(() => window.dispatchEvent(new Event(FOOTER_RELOCATED)));
    return () => cancelAnimationFrame(frame);
  }, [host, inOverflow, overflowTarget, concealed]);
  useLayoutEffect(() => () => host.remove(), [host]);
  const description =
    label ??
    content.current?.querySelector("button")?.getAttribute("aria-label") ??
    content.current?.querySelector("button")?.textContent ??
    id;
  return (
    <>
      <div
        ref={anchor}
        data-footer-part={id}
        data-footer-hidden={hidden.includes(id) ? "overflow" : empty.includes(id) ? "empty" : undefined}
        aria-hidden={concealed || undefined}
        inert={concealed}
        className={cn(
          "inline-flex w-max shrink-0 items-center",
          concealed && "invisible pointer-events-none absolute top-0 left-0",
          className,
        )}
      />
      {createPortal(
        <div className={inOverflow ? "flex w-full flex-col items-start gap-1 rounded px-1 py-1" : "inline-flex"}>
          {inOverflow && <span className="w-full min-w-0 break-words text-xs">{description}</span>}
          <div ref={content} data-footer-content={id} className="inline-flex w-max shrink-0 items-center text-ui">
            <FooterVisibility value={!concealed || inOverflow}>{children}</FooterVisibility>
          </div>
        </div>,
        host,
      )}
    </>
  );
}

/** Optional attribution loses visual width before any control; no decorative icon replaces it. */
export function FooterCredit({ children }: { children: string }) {
  const { compactCredit } = useFooterLayout();
  return (
    <span className="inline-flex whitespace-nowrap text-ui-sm" title={children}>
      <span className="overflow-hidden" style={compactCredit ? { width: 0 } : undefined}>
        <span data-footer-credit-text className="inline-block whitespace-pre">
          {children}
        </span>
      </span>
    </span>
  );
}

function containsControlled(root: HTMLElement | null, target: EventTarget | null): boolean {
  if (!(target instanceof Node) || !root) return false;
  const pending = [root],
    seen = new Set<HTMLElement>();
  while (pending.length) {
    const item = pending.pop();
    if (!item || seen.has(item)) continue;
    if (item.contains(target)) return true;
    seen.add(item);
    for (const control of item.querySelectorAll("[aria-controls]"))
      for (const id of (control.getAttribute("aria-controls") ?? "").split(/\s+/)) {
        const pane = document.getElementById(id);
        if (pane) pending.push(pane);
      }
  }
  return false;
}

/** Only genuinely hidden controls move here; their original mounted instances remain authoritative. */
export function FooterMore() {
  const { hidden, overflowOpen, setOverflowOpen, setOverflowTarget } = useFooterLayout();
  const trigger = useRef<HTMLButtonElement>(null);
  const pane = useRef<HTMLDivElement>(null);
  const reposition = useRef<(() => void) | null>(null);
  const [at, setAt] = useState({ left: 0, top: 0, maxHeight: 400 });
  const id = useId();
  const open = overflowOpen && hidden.length > 0;
  const panelRef = useCallback(
    (element: HTMLDivElement | null) => {
      pane.current = element;
      setOverflowTarget(element);
    },
    [setOverflowTarget],
  );
  useLayoutEffect(() => {
    if (!hidden.length && overflowOpen) setOverflowOpen(false);
  }, [hidden.length, overflowOpen, setOverflowOpen]);
  useLayoutEffect(() => {
    if (!open || !pane.current || !trigger.current) return;
    const panel = pane.current;
    const position = () => {
      if (!trigger.current) return;
      const size = panel.getBoundingClientRect();
      const maxHeight = popoverHeight(trigger.current.getBoundingClientRect());
      const next = {
        ...placePopover(
          trigger.current.getBoundingClientRect(),
          { width: size.width, height: Math.min(size.height, maxHeight) },
          "above",
        ),
        maxHeight,
      };
      setAt((old) => (old.left === next.left && old.top === next.top && old.maxHeight === next.maxHeight ? old : next));
    };
    reposition.current = position;
    position();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(position);
    observer?.observe(panel);
    const unwatchViewport = watchViewport(position);
    window.addEventListener(FOOTER_RELOCATED, position);
    window.addEventListener("scroll", position, true);
    const frame = requestAnimationFrame(() => panel.querySelector<HTMLElement>("button,a,input")?.focus());
    const outside = (event: MouseEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !containsControlled(panel, event.target))
        setOverflowOpen(false);
    };
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) {
        event.preventDefault();
        setOverflowOpen(false);
        trigger.current?.focus();
      }
    };
    window.addEventListener("mousedown", outside);
    window.addEventListener("keydown", keydown);
    return () => {
      reposition.current = null;
      observer?.disconnect();
      cancelAnimationFrame(frame);
      unwatchViewport();
      window.removeEventListener(FOOTER_RELOCATED, position);
      window.removeEventListener("scroll", position, true);
      window.removeEventListener("mousedown", outside);
      window.removeEventListener("keydown", keydown);
    };
  }, [open, setOverflowOpen]);
  useLayoutEffect(() => {
    if (open) reposition.current?.();
  });
  return (
    <FooterPart id={FOOTER_MORE}>
      <button
        ref={trigger}
        type="button"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={t("status.more")}
        title={t("status.more")}
        onClick={() => setOverflowOpen(!open)}
        className="flex size-6 items-center justify-center rounded hover:bg-accent/60 hover:text-foreground"
      >
        <Ellipsis aria-hidden className="size-4" />
      </button>
      {open &&
        createPortal(
          <section
            id={id}
            ref={panelRef}
            aria-label={t("status.more")}
            className="viewport-popover fixed z-(--layer-popover) flex w-72 max-w-[calc(var(--hz-viewport-width,100vw)-2rem)] flex-col overflow-y-auto rounded-xl border border-border bg-popover p-2 text-sm text-foreground shadow-xl"
            style={at}
          />,
          document.body,
        )}
    </FooterPart>
  );
}
