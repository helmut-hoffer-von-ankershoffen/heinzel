import { useEffect, useState } from "react";

/** The desktop app's title bar row: its height and the room the window controls take on either side (px). */
export type Titlebar = { height: number; left: number; right: number };

type Bridge = {
  titlebar?: Titlebar | null;
  onFullscreen?: (callback: (full: boolean) => void) => (() => void) | undefined;
};

function bridge(): Bridge | undefined {
  return (window as Window & { heinzelDesktop?: Bridge }).heinzelDesktop;
}

/** The desktop bridge's title bar, or null in a browser or PWA, whose layout stays as it is. */
export function desktopTitlebar(): Titlebar | null {
  const bar = bridge()?.titlebar;
  return bar && typeof bar.height === "number" && bar.height > 0 ? bar : null;
}

/** The title bar and whether the window is full screen, where the traffic lights and their inset are gone. */
export function useTitlebar(): { bar: Titlebar | null; fullscreen: boolean } {
  const [bar] = useState(desktopTitlebar);
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => {
    if (!bar) return;
    const stop = bridge()?.onFullscreen?.(setFullscreen);
    return typeof stop === "function" ? stop : undefined;
  }, [bar]);
  return { bar, fullscreen };
}

/** Width of the collapsed navigation rail (w-14), which the expand button in the title bar row overhangs. */
const RAIL = 56;
/** The expand or collapse button and its gap after the traffic lights. */
const TOGGLE = 40;

/** Writes the title bar's CSS variables on <html>; a browser gets 0 everywhere and no data-desktop-titlebar. */
export function applyTitlebar(root: HTMLElement, bar: Titlebar | null, fullscreen: boolean, navOpen: boolean) {
  const left = bar && !fullscreen ? bar.left : 0;
  const right = bar && !fullscreen ? bar.right : 0;
  root.style.setProperty("--titlebar-height", `${bar ? bar.height : 0}px`);
  root.style.setProperty("--titlebar-inset-left", `${left}px`);
  root.style.setProperty("--titlebar-inset-right", `${right}px`);
  // The main header starts after the expand button when that overhangs the collapsed rail.
  root.style.setProperty("--titlebar-main-start", `${bar && !navOpen ? Math.max(0, left + TOGGLE - RAIL) : 0}px`);
  if (bar) root.dataset.desktopTitlebar = "";
  else delete root.dataset.desktopTitlebar;
}
