import { type RefObject, useEffect, useRef } from "react";
import { viewportBounds } from "@/lib/viewport";

/** One desktop bridge for a native surface: the sidebar browser's or the media player's. */
export type SurfaceBridge = (request: Record<string, unknown>) => Promise<unknown>;

const OVERLAYS =
  '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [role="tooltip"], dialog[open], .viewport-popover, [popover]';

/**
 * Keep a desktop guest view exactly over `surface` while it is visible, and hidden for product overlays, hidden
 * panels, background tabs or any position outside the viewport. `poll` runs with each 500 ms refresh.
 */
export function useNativeSurface(
  surface: RefObject<HTMLElement | null>,
  active: boolean,
  send: SurfaceBridge | undefined,
  poll?: () => void,
): void {
  const refresh = useRef(poll);
  refresh.current = poll;
  useEffect(() => {
    if (!send) return;
    const hide = () => void send({ action: "hide" }).catch(() => {});
    const update = () => {
      const element = surface.current;
      const overlay = Array.from(document.querySelectorAll(OVERLAYS)).some(
        (node) => node.getClientRects().length > 0 && !node.closest("[hidden], [inert]"),
      );
      if (!active || !element || element.closest("[hidden], [inert]") || overlay || document.hidden) {
        hide();
        return;
      }
      const box = element.getBoundingClientRect();
      // The native surface must stay inside the visible viewport; anything else hides it rather than overflowing.
      const view = viewportBounds();
      if (
        box.width < 1 ||
        box.height < 1 ||
        box.x < view.left ||
        box.y < view.top ||
        box.right > view.left + view.width ||
        box.bottom > view.top + view.height
      ) {
        hide();
        return;
      }
      void send({
        action: "layout",
        bounds: {
          x: Math.round(box.x),
          y: Math.round(box.y),
          width: Math.floor(box.width),
          height: Math.floor(box.height),
        },
      }).catch(() => {});
    };
    const observer = new ResizeObserver(update);
    if (surface.current) observer.observe(surface.current);
    const changes = new MutationObserver(update);
    changes.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["hidden", "inert", "open", "role", "class", "style"],
    });
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    document.addEventListener("visibilitychange", update);
    document.addEventListener("toggle", update, true);
    const timer = window.setInterval(() => {
      refresh.current?.();
      update();
    }, 500);
    update();
    return () => {
      observer.disconnect();
      changes.disconnect();
      window.clearInterval(timer);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      document.removeEventListener("visibilitychange", update);
      document.removeEventListener("toggle", update, true);
      hide();
    };
  }, [active, send, surface]);
}
