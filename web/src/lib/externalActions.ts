import { type ExternalAction, externalActionUrl } from "../../../desktop/src/external-links.js";

export type { ExternalAction };

function desktopBridge(): { externalLinks?: boolean } | undefined {
  return (window as Window & { heinzelDesktop?: { externalLinks?: boolean } }).heinzelDesktop;
}

/** Browsers keep ordinary links. Desktop offers only the actions its installed shell supports. */
export function externalHref(action: ExternalAction, href: string | null): string | null {
  if (!href) return null;
  const desktop = desktopBridge();
  return desktop ? (desktop.externalLinks ? externalActionUrl(action, href) : null) : href;
}

export function usesNativeExternalLinks(): boolean {
  return desktopBridge()?.externalLinks === true;
}
