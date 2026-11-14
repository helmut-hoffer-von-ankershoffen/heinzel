// Settings > "Open links in the built-in browser": a switch in the desktop app, on by default. The page keeps the
// choice and tells the desktop, which applies it to the next link with no restart. Outside the desktop app a link
// opens a new browser tab as it always did.
const KEY = "hz.links-in-browser";

type LinkBridge = { linkMode?: (inBrowser: boolean) => Promise<boolean> };
const bridge = () => (window as Window & { heinzelDesktop?: LinkBridge }).heinzelDesktop;

/** Whether this window is the desktop app, the only place the switch means anything. */
export const linkModeAvailable = (): boolean => typeof bridge()?.linkMode === "function";

export function linksInBuiltInBrowser(): boolean {
  try {
    return localStorage.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

/** Tell the desktop the stored choice, once the page is up and after every change. */
export function syncLinkMode(): void {
  void bridge()
    ?.linkMode?.(linksInBuiltInBrowser())
    .catch(() => {});
}

export function setLinksInBuiltInBrowser(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    // The choice then lasts until the page reloads; the desktop still applies it now.
  }
  void bridge()
    ?.linkMode?.(on)
    .catch(() => {});
}
