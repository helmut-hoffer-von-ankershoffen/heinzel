import { type RefObject, useEffect, useRef, useState } from "react";
import { recordSurface } from "@/lib/surfaceDiagnostics";
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
  name?: "browser" | "player",
  supportsPlayerCutout = false,
): boolean {
  const [bridgeFailed, setBridgeFailed] = useState(false);
  const refresh = useRef(poll);
  refresh.current = poll;
  useEffect(() => {
    if (!send) {
      setBridgeFailed(false);
      if (name) recordSurface(name, "bridge unavailable", "unavailable", null);
      return;
    }
    let generation = 0;
    let applied = 0;
    let diagnosticRequest = 0;
    let diagnosticApplied = 0;
    let disposed = false;
    let bridge = "pending";
    let decision = "inactive";
    const record = () => {
      if (name) recordSurface(name, decision, bridge);
    };
    const dispatch = (action: Record<string, unknown>, why: string) => {
      decision = why;
      record();
      const request = ++generation;
      void send(action).then(
        () => {
          if (disposed || request < applied) return;
          applied = request;
          bridge = "ok";
          setBridgeFailed(false);
          record();
        },
        () => {
          if (disposed || request < applied) return;
          applied = request;
          bridge = "failed";
          setBridgeFailed(true);
          record();
        },
      );
    };
    const hide = (why: string) => dispatch({ action: "hide" }, why);
    const update = () => {
      const element = surface.current;
      if (!active || !element || element.closest("[hidden], [inert]") || document.hidden) {
        let reason = "hidden";
        if (!active) reason = "inactive";
        else if (document.hidden) reason = "background";
        hide(reason);
        return;
      }
      const box = element.getBoundingClientRect();
      // The player card contains its own surface and can sit beside another. Other overlays still hide every
      // native view, including their backdrops, so a modal cannot leave a guest interactive behind it.
      let cutout: { x: number; y: number; width: number; height: number } | undefined;
      const overlay = Array.from(document.querySelectorAll(OVERLAYS)).some((node) => {
        if (node.closest("[hidden], [inert]")) return false;
        if (!node.hasAttribute("data-native-surface-container")) return node.getClientRects().length > 0;
        if (node.contains(element)) return false;
        for (const rect of node.getClientRects()) {
          const left = Math.floor(Math.max(rect.left, box.left));
          const top = Math.floor(Math.max(rect.top, box.top));
          const right = Math.ceil(Math.min(rect.right, box.right));
          const bottom = Math.ceil(Math.min(rect.bottom, box.bottom));
          if (rect.left >= box.right || rect.right <= box.left || rect.top >= box.bottom || rect.bottom <= box.top)
            continue;
          if (!supportsPlayerCutout || name !== "browser" || node.getAttribute("data-dock") !== "floating" || cutout)
            return true;
          cutout = { x: left, y: top, width: right - left, height: bottom - top };
        }
        return false;
      });
      if (overlay) {
        hide("overlay");
        return;
      }
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
        hide("outside viewport");
        return;
      }
      dispatch(
        {
          action: "layout",
          ...(cutout ? { playerCutout: cutout } : {}),
          bounds: {
            x: Math.round(box.x),
            y: Math.round(box.y),
            width: Math.floor(box.width),
            height: Math.floor(box.height),
          },
        },
        "layout requested",
      );
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
      if (name) {
        const sample = ++diagnosticRequest;
        void send({ action: "diagnostics" })
          .then((answer) => {
            if (disposed || sample < diagnosticApplied || !answer || typeof answer !== "object") return;
            const value = answer as { focused?: unknown; visible?: unknown; status?: unknown; reason?: unknown };
            const statuses = ["empty", "loading", "ready", "unavailable", "unsupported"];
            const reasons = [
              "no guest",
              "unfocused",
              "unavailable",
              "blank",
              "hidden by frontend",
              "draw-enabled",
              "hidden",
              "unknown",
            ];
            if (
              typeof value.focused !== "boolean" ||
              (typeof value.visible !== "boolean" && value.visible !== null) ||
              typeof value.status !== "string" ||
              !statuses.includes(value.status)
            )
              return;
            diagnosticApplied = sample;
            recordSurface(name, decision, bridge, {
              focused: value.focused,
              visible: value.visible,
              status: value.status,
              reason: typeof value.reason === "string" && reasons.includes(value.reason) ? value.reason : "unknown",
            });
          })
          .catch(() => {});
      }
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
      hide("inactive");
      disposed = true;
      if (name) recordSurface(name, "inactive", bridge, null);
    };
  }, [active, send, surface, name, supportsPlayerCutout]);
  return bridgeFailed;
}
