// Only a window created by this shell and its current main document may ask for lifecycle state.
export function createLifecycleHandler({ windows, fromWebContents, connection, lifecycle }) {
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
    return lifecycle(connection());
  };
}
import { isOwnedUiNavigation } from "./ui-navigation.js";
