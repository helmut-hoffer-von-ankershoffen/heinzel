import { parseConnection } from "./connection.js";
import { externalActionUrl } from "./external-links.js";

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

/** Embedded frames keep the web head's CSP. Popups from any frame are denied without side effects. */
export function guardUiNavigation(contents, connection) {
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  contents.on("will-navigate", (event) => {
    if (!isOwnedUiNavigation(event.url, connection)) event.preventDefault();
  });
  contents.on("will-redirect", (event) => {
    if (event.isMainFrame !== false && !isOwnedUiNavigation(event.url, connection)) event.preventDefault();
  });
}

/** Only the owned main frame's isolated preload can request an external action after a trusted click. */
export function handleExternalActions(contents, connection, openExternal) {
  contents.ipc.handle("hz:open-external", async (event, action, target) => {
    if (event.sender !== contents || contents.isDestroyed()) return false;
    const frame = event.senderFrame;
    const main = contents.mainFrame;
    if (!frame || !main || frame.isDestroyed() || main.isDestroyed() || frame.detached ||
        frame.processId !== main.processId || frame.frameToken !== main.frameToken ||
        !isOwnedUiNavigation(frame.url, connection())) return false;
    const url = externalActionUrl(action, target);
    if (!url) return false;
    try {
      await openExternal(url);
      return true;
    } catch {
      // An OS error may contain the meeting URL. Keep it out of Electron's IPC error logs.
      return false;
    }
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
