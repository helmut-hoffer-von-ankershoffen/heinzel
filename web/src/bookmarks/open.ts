// Opening a bookmark: a tab of the built-in browser in the desktop app; elsewhere the link opens a new browser tab.
import { requestMenuAction } from "@/about/menu";

type Bridge = { browser: (request: Record<string, unknown>) => Promise<unknown> };
function bridge(): Bridge | undefined {
  const value = (window as Window & { heinzelDesktop?: Partial<Bridge> }).heinzelDesktop;
  return typeof value?.browser === "function" ? (value as Bridge) : undefined;
}

/** Whether a click should stay in the app (the desktop's built-in browser) instead of following the link. */
export const opensInApp = (): boolean => bridge() !== undefined;

/** Open `url` in a new tab of the built-in browser and show the panel; a full tab strip still shows the panel. */
export async function openInApp(url: string): Promise<void> {
  try {
    await bridge()?.browser({ action: "new-tab", url });
  } catch {
    // All tabs in use, or an address the browser refuses: the panel opens and the person sees its state.
  }
  requestMenuAction("browser-show");
}
