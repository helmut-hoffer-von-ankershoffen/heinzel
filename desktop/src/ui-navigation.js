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

// Where a link with target=_blank may go: the meeting tools and calendar pages the calendar pane offers (Join, Open in
// calendar). Anything else stays denied. Always https, no credentials, no port.
const EXTERNAL_HOSTS = new Set(["zoom.us", "meet.google.com", "teams.microsoft.com", "teams.live.com", "calendar.google.com"]);
const EXTERNAL_SUFFIXES = [".zoom.us", ".webex.com"];

/** True for an https link the app may hand to the system's default handler. */
export function isAllowedExternal(target) {
  try {
    const url = new URL(target);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return false;
    const host = url.hostname.toLowerCase();
    if (host === "www.google.com") return url.pathname.startsWith("/calendar/");
    return EXTERNAL_HOSTS.has(host) || EXTERNAL_SUFFIXES.some((suffix) => host.endsWith(suffix));
  } catch {
    return false;
  }
}

/**
 * Embedded third-party frames keep the web head's CSP; only top-level documents use this guard. A new window is never
 * created: an allow-listed link (Join, Open in calendar) goes to the system handler through `openExternal`.
 */
export function guardUiNavigation(contents, connection, openExternal = () => {}) {
  contents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternal(url)) openExternal(url);
    return { action: "deny" };
  });
  contents.on("will-navigate", (event) => {
    if (!isOwnedUiNavigation(event.url, connection)) event.preventDefault();
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
