import { isIP } from "node:net";
import { LOCAL_NAME } from "./mini-browser-network.js";
import { isOwnedUiNavigation } from "./ui-navigation.js";

/** Admit HTTPS destinations, never credentials, local service routes or external protocol handlers. */
export function browserUrl(value, productOrigin) {
  if (typeof value !== "string" || value.length > 4096 || /[\u0000-\u0020\u007f]/u.test(value))
    throw new Error("Enter a public HTTPS address without credentials.");
  const url = new URL(value);
  const host = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  // Literal addresses and local DNS suffixes are deliberately outside this first browser surface.
  if (url.protocol !== "https:" || (url.port && url.port !== "443") || url.username || url.password || url.origin === productOrigin ||
      isIP(host) || !host.includes(".") || host.endsWith(".") ||
      LOCAL_NAME.test(host))
    throw new Error("Enter a public HTTPS address without credentials.");
  return url.href;
}

/** Only the currently owned product document may control native browser surfaces. */
export function browserOwner(event, { windows, fromWebContents, connection }) {
  const window = fromWebContents(event.sender);
  const frame = event.senderFrame;
  const main = event.sender.isDestroyed() ? null : event.sender.mainFrame;
  if (!window || !windows.has(window) || window.isDestroyed() || !frame || !main ||
      frame.isDestroyed() || main.isDestroyed() || frame.detached ||
      frame.processId !== main.processId || frame.frameToken !== main.frameToken ||
      !isOwnedUiNavigation(frame.url, connection())) throw new Error("Unknown window or frame.");
  return window;
}

/** True when an object has exactly these own keys, in any order; no sort, so no locale or code-unit ordering question. */
export function hasExactKeys(value, expected) {
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) => Object.hasOwn(value, key));
}

/** Reject rather than clamp off-window rectangles; DOM geometry is not permission to cover other windows. */
export function browserBounds(value, size) {
  if (!value || typeof value !== "object" || !hasExactKeys(value, ["height", "width", "x", "y"]) ||
      ![value.x, value.y, value.width, value.height].every(Number.isSafeInteger) ||
      value.x < 0 || value.y < 0 || value.width < 1 || value.height < 1 ||
      value.x + value.width > size[0] || value.y + value.height > size[1])
    throw new Error("Browser panel geometry is unavailable.");
  return { x: value.x, y: value.y, width: value.width, height: value.height };
}
