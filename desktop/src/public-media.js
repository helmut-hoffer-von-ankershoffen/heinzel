import { isOwnedUiNavigation } from "./ui-navigation.js";

/** Only canonical public raster captures and explicit YouTube watch identities can leave the shell. */
export function publicMediaUrl(target) {
  if (typeof target !== "string" || target.length > 1024) return null;
  try {
    const url = new URL(target);
    if (url.href !== target || url.protocol !== "https:" || url.username || url.password || url.port || url.hash) return null;
    const video = /^\?v=([A-Za-z0-9_-]{11})$/.exec(url.search);
    if (url.hostname === "www.youtube.com" && url.pathname === "/watch" && video) {
      const watch = new URL("https://www.youtube.com/watch");
      watch.searchParams.set("v", video[1]);
      return watch.href;
    }
    if (url.hostname !== "raw.githubusercontent.com" || url.search) return null;
    const path = url.pathname.slice(1);
    if (!/^[A-Za-z0-9_./-]+$/.test(path) || path.split("/").some((part) => !part || part === "." || part === "..")) return null;
    if (!/^[A-Za-z0-9_-][A-Za-z0-9_.-]*\/[A-Za-z0-9_-][A-Za-z0-9_.-]*\/[a-f0-9]{40}\/.+\.(png|jpg|webp)$/.test(path)) return null;
    // Construct the outgoing URL with its own fixed authority; the renderer supplies only the validated path.
    const raster = new URL("https://raw.githubusercontent.com/");
    raster.pathname = "/" + path;
    return raster.href;
  } catch {
    return null;
  }
}

/** The page has no opening API; the isolated preload sends only trusted, activated link clicks here. */
export function createPublicMediaHandler({ windows, fromWebContents, connection, openExternal }) {
  return async (event, target) => {
    try {
      const window = fromWebContents(event.sender);
      const frame = event.senderFrame;
      const main = event.sender.isDestroyed() ? null : event.sender.mainFrame;
      if (!window || !windows.has(window) || window.isDestroyed() || !frame || !main ||
          frame.isDestroyed() || main.isDestroyed() || frame.detached ||
          frame.processId !== main.processId || frame.frameToken !== main.frameToken ||
          !isOwnedUiNavigation(frame.url, connection())) return false;
      const checked = publicMediaUrl(target);
      if (!checked) return false;
      await openExternal(checked);
      return true;
    } catch {
      return false;
    }
  };
}
