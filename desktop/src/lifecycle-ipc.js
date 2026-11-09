import { isOwnedUiNavigation } from "./ui-navigation.js";

// Only a window created by this shell and its current main document may use the product lifecycle bridge.
export function ownedLifecycleWindow(event, { windows, fromWebContents, connection }) {
  const window = fromWebContents(event.sender);
  const frame = event.senderFrame;
  const main = event.sender.isDestroyed() ? null : event.sender.mainFrame;
  const current = connection();
  if (!window || !windows.has(window) || window.isDestroyed() || !frame || !main ||
      frame.isDestroyed() || main.isDestroyed() || frame.detached ||
      frame.processId !== main.processId || frame.frameToken !== main.frameToken ||
      !isOwnedUiNavigation(frame.url, current)) {
    throw new Error("Unknown window or frame.");
  }
  return { window, connection: current };
}

export function createLifecycleHandler(options) {
  return async (event) => options.lifecycle(ownedLifecycleWindow(event, options).connection);
}
