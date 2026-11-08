import { parseConnection } from "./connection.js";

/** Use the existing web head's fragment authentication; never attach a bearer to another origin. */
export function authenticatedUiUrl(connection) {
  const url = `${connection.origin}/#token=${connection.token}`;
  const checked = parseConnection([`--hz-url=${url}`]);
  if (checked.origin !== connection.origin || checked.token !== connection.token)
    throw new Error("Invalid local UI connection.");
  return url;
}

/** A full navigation or redirect may remain only on the owned local service. */
export function isOwnedUiNavigation(target, connection) {
  try {
    const owned = new URL(authenticatedUiUrl(connection));
    const url = new URL(target);
    return url.origin === owned.origin && !url.username && !url.password;
  } catch {
    return false;
  }
}

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "0.0.0.0", "[::]"]);

/**
 * Classify a link target from the product UI: "app" stays in the window, "external" opens in the
 * default browser, "deny" does nothing. External means a public http(s) URL without credentials, or
 * a mailto: address; local services, other schemes and unparsable strings are denied.
 */
export function classifyUiLink(target, connection) {
  if (typeof target !== "string" || target.length > 8192) return { kind: "deny" };
  if (isOwnedUiNavigation(target, connection)) return { kind: "app" };
  let url;
  try {
    url = new URL(target);
  } catch {
    return { kind: "deny" };
  }
  if (url.protocol === "mailto:") return url.pathname ? { kind: "external", url: url.href } : { kind: "deny" };
  if (url.protocol !== "https:" && url.protocol !== "http:") return { kind: "deny" };
  if (url.username || url.password || !url.hostname) return { kind: "deny" };
  const host = url.hostname.toLowerCase();
  if (LOOPBACK_HOSTS.has(host) || host.endsWith(".localhost") || /^127\./.test(host)) return { kind: "deny" };
  return { kind: "external", url: url.href };
}

/** Hand a classified external URL to the OS; never throws into Electron's event loop. */
export function openUiLinkExternally(target, connection, openExternal) {
  const link = classifyUiLink(target, connection);
  if (link.kind !== "external") return false;
  Promise.resolve().then(() => openExternal(link.url)).catch(() => {});
  return true;
}

/**
 * Only top-level documents use this guard; embedded third-party frames keep the web head's CSP.
 * The window never leaves the owned origin and never opens a child window: target=_blank,
 * window.open() and middle-click on a public link go to the default browser instead.
 */
export function guardUiNavigation(contents, connection, openExternal) {
  contents.setWindowOpenHandler((details) => {
    openUiLinkExternally(details?.url, connection, openExternal);
    return { action: "deny" };
  });
  contents.on("will-navigate", (event) => {
    if (isOwnedUiNavigation(event.url, connection)) return;
    event.preventDefault();
    openUiLinkExternally(event.url, connection, openExternal);
  });
  contents.on("will-redirect", (event) => {
    if (event.isMainFrame !== false && !isOwnedUiNavigation(event.url, connection)) event.preventDefault();
  });
}

/** Do not open or show a product window after shutdown began, including across asynchronous startup. */
export async function openProductUi({ connection, lifecycle, createWindow, prepareWindow, cancelled }) {
  await lifecycle(connection);
  if (cancelled()) throw new Error("Desktop startup was cancelled.");
  const window = createWindow();
  try {
    prepareWindow(window);
    await window.loadURL(authenticatedUiUrl(connection));
    if (cancelled() || window.isDestroyed()) throw new Error("Desktop startup was cancelled.");
    window.show();
    return window;
  } catch (error) {
    if (!window.isDestroyed()) window.destroy();
    throw error;
  }
}
