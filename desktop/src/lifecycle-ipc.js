import { isOwnedUiNavigation } from "./ui-navigation.js";
import { aboutLinkUrl } from "./app-menu.js";

// Only a window created by this shell and its current main document may ask for state.
function ownedHandler({ windows, fromWebContents, connection }, read) {
  return async (event, ...args) => {
    const window = fromWebContents(event.sender);
    const frame = event.senderFrame;
    const main = event.sender.isDestroyed() ? null : event.sender.mainFrame;
    if (!window || !windows.has(window) || window.isDestroyed() || !frame || !main ||
        frame.isDestroyed() || main.isDestroyed() || frame.detached ||
        frame.processId !== main.processId || frame.frameToken !== main.frameToken ||
        !isOwnedUiNavigation(frame.url, connection())) {
      throw new Error("Unknown window or frame.");
    }
    return read(connection(), ...args);
  };
}

export function createLifecycleHandler({ lifecycle, ...owner }) {
  return ownedHandler(owner, lifecycle);
}

/** The desktop app's own version and install location, for the About dialog; nothing secret. */
export function createAboutHandler({ about, ...owner }) {
  return ownedHandler(owner, () => about());
}

/** Open only About's project page or a versioned release from the current owned UI. */
export function createAboutLinkHandler({ open, log, ...owner }) {
  return ownedHandler(owner, async (_connection, action, version) => {
    const key = action === "release" || action === "repository" ? action : "unknown";
    const report = (outcome) => log({ event: "desktop.about.link", action: key, outcome });
    report("start");
    const url = aboutLinkUrl(action, version);
    if (!url) {
      report("blocked");
      return false;
    }
    try {
      await open(url);
      report("opened");
      return true;
    } catch {
      report("failed");
      return false;
    }
  });
}
