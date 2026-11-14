// "Open links in the built-in browser": a setting of the desktop app itself, kept by its main process (on by default),
// so a reset of the page's storage or a changed port never turns it back on. The page only reads and writes it
// through the desktop bridge; outside the desktop app a link opens a new browser tab as it always did.
type LinkBridge = { linkMode?: (inBrowser?: boolean) => Promise<boolean> };
const bridge = () => (window as Window & { heinzelDesktop?: LinkBridge }).heinzelDesktop;

/** Whether this window is the desktop app, the only place the switch means anything. */
export const linkModeAvailable = (): boolean => typeof bridge()?.linkMode === "function";

/** The stored choice; it applies to the next link at once, with no restart. */
export const readLinkMode = (): Promise<boolean> => bridge()?.linkMode?.() ?? Promise.resolve(true);

/** Store the choice and return what the desktop now holds, which is the old value if it could not be applied. */
export const writeLinkMode = (inBrowser: boolean): Promise<boolean> =>
  bridge()?.linkMode?.(inBrowser) ?? Promise.resolve(inBrowser);
