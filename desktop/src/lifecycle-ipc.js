import { isOwnedUiNavigation } from "./ui-navigation.js";

// Only a window created by this shell and its current main document may ask for state.
function ownedHandler({ windows, fromWebContents, connection }, read) {
  return async (event) => {
    const window = fromWebContents(event.sender);
    const frame = event.senderFrame;
    const main = event.sender.isDestroyed() ? null : event.sender.mainFrame;
    if (!window || !windows.has(window) || window.isDestroyed() || !frame || !main ||
        frame.isDestroyed() || main.isDestroyed() || frame.detached ||
        frame.processId !== main.processId || frame.frameToken !== main.frameToken ||
        !isOwnedUiNavigation(frame.url, connection())) {
      throw new Error("Unknown window or frame.");
    }
    return read(connection());
  };
}

export function createLifecycleHandler({ lifecycle, ...owner }) {
  return ownedHandler(owner, lifecycle);
}

/** The desktop app's own version and install location, for the About dialog; nothing secret. */
export function createAboutHandler({ about, ...owner }) {
  return ownedHandler(owner, () => about());
}
