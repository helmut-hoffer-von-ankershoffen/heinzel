// Fixed overlays follow the visible browser area, including keyboard and pinch-zoom changes on phones.
import { useLayoutEffect } from "react";

function visibleBounds() {
  const viewport = window.visualViewport;
  return {
    left: viewport?.offsetLeft ?? 0,
    top: viewport?.offsetTop ?? 0,
    width: viewport?.width ?? window.innerWidth,
    height: viewport?.height ?? window.innerHeight,
  };
}

/** The visible area inside device safe insets, for floating controls. */
export function viewportBounds() {
  const bounds = visibleBounds();
  const style = getComputedStyle(document.documentElement);
  const inset = (edge: string) => Number.parseFloat(style.getPropertyValue(`--hz-viewport-safe-${edge}`)) || 0;
  const left = inset("left"),
    right = inset("right"),
    top = inset("top"),
    bottom = inset("bottom");
  return {
    left: bounds.left + left,
    top: bounds.top + top,
    width: Math.max(0, bounds.width - left - right),
    height: Math.max(0, bounds.height - top - bottom),
  };
}

export function watchViewport(update: () => void): () => void {
  window.addEventListener("resize", update);
  window.visualViewport?.addEventListener("resize", update);
  window.visualViewport?.addEventListener("scroll", update);
  return () => {
    window.removeEventListener("resize", update);
    window.visualViewport?.removeEventListener("resize", update);
    window.visualViewport?.removeEventListener("scroll", update);
  };
}

/** Share the visible bounds with CSS-sized dialogs and popovers. */
export function useVisualViewport(): void {
  useLayoutEffect(() => {
    const root = document.documentElement;
    const update = () => {
      for (const [key, value] of Object.entries(visibleBounds()))
        root.style.setProperty(`--hz-viewport-${key}`, `${value}px`);
      root.dataset.keyboardOpen = String(
        visibleBounds().height * (window.visualViewport?.scale ?? 1) < window.innerHeight - 100,
      );
      // Keyboard changes reduce available layout height; pinch zoom alone must not reflow the workspace.
      root.style.setProperty("--hz-layout-height", `${visibleBounds().height * (window.visualViewport?.scale ?? 1)}px`);
    };
    update();
    const unwatch = watchViewport(update);
    return () => {
      unwatch();
      for (const key of ["left", "top", "width", "height"]) root.style.removeProperty(`--hz-viewport-${key}`);
      root.style.removeProperty("--hz-layout-height");
      delete root.dataset.keyboardOpen;
    };
  }, []);
}
