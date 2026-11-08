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

const LOCAL_SUFFIXES = [".localhost", ".local", ".internal", ".lan", ".home.arpa"];
const MAILTO_PARAMS = new Set(["subject", "body", "cc", "bcc"]);
const MAILTO_ADDRESS = /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/;
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;
const MAX_URL = 8192;
const MAX_MAILTO = 2000;

function ipv4Octets(host) {
  const parts = host.split(".");
  if (parts.length !== 4 || !parts.every((part) => /^\d{1,3}$/.test(part))) return null;
  const octets = parts.map(Number);
  return octets.every((octet) => octet <= 255) ? octets : null;
}

/** Public IPv4 only: loopback, 0/8, private, CGNAT (tailnet), link-local, multicast and reserved are local. */
function isLocalIpv4([a, b]) {
  return a === 0 || a === 10 || a === 127 || a >= 224 || (a === 100 && b >= 64 && b <= 127)
    || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

/** Expand a URL-normalised IPv6 literal (no brackets) into eight 16-bit groups. */
function ipv6Groups(host) {
  let text = host;
  const v4 = text.match(/^(.*:)(\d+\.\d+\.\d+\.\d+)$/);
  if (v4) {
    const octets = ipv4Octets(v4[2]);
    if (!octets) return null;
    text = `${v4[1]}${((octets[0] << 8) | octets[1]).toString(16)}:${((octets[2] << 8) | octets[3]).toString(16)}`;
  }
  const halves = text.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const fill = halves.length === 2 ? 8 - head.length - tail.length : 0;
  if (fill < 0) return null;
  const groups = [...head, ...Array(fill).fill("0"), ...tail];
  if (groups.length !== 8 || !groups.every((group) => /^[0-9a-f]{1,4}$/i.test(group))) return null;
  return groups.map((group) => Number.parseInt(group, 16));
}

/** Only global unicast IPv6 outside unique-local (incl. tailnet fd7a::/16) and link-local is public. */
function isLocalIpv6(groups) {
  const [first] = groups;
  if (groups.slice(0, 5).every((group) => group === 0) && (groups[5] === 0 || groups[5] === 0xffff)) return true;
  // NAT64, 6to4 and Teredo embed an IPv4 address the browser may reach locally: never treat them as public.
  if ((groups[0] === 0x64 && groups[1] === 0xff9b) || groups[0] === 0x2002 || (groups[0] === 0x2001 && groups[1] === 0)) return true;
  return (first & 0xe000) !== 0x2000 || first === 0x2001 && groups[1] === 0x0db8;
}

/** Decide on the URL parser's normalised hostname, never the raw input string. */
export function isPublicHost(hostname) {
  let host = hostname.toLowerCase();
  if (host.startsWith("[")) {
    const groups = host.endsWith("]") ? ipv6Groups(host.slice(1, -1)) : null;
    return groups !== null && !isLocalIpv6(groups);
  }
  host = host.replace(/\.+$/, "");
  if (!host || host.startsWith(".")) return false;
  const octets = ipv4Octets(host);
  if (octets) return !isLocalIpv4(octets);
  if (/^[\d.]+$/.test(host)) return false;
  if (!host.includes(".") || host === "localhost") return false;
  return !LOCAL_SUFFIXES.some((suffix) => host.endsWith(suffix));
}

function decodeStrict(text) {
  try {
    const value = decodeURIComponent(text);
    return CONTROL.test(value) ? null : value;
  } catch {
    return null;
  }
}

/** mailto: with valid address(es) and only subject, body, cc and bcc; rebuilt from decoded parts. */
export function normaliseMailto(url) {
  const [rawTo, ...rest] = url.href.slice("mailto:".length).split("?");
  if (rest.length > 1) return null;
  const parts = [];
  const addresses = (value) => {
    const list = value.split(",").map((item) => item.trim()).filter(Boolean);
    return list.length && list.every((item) => MAILTO_ADDRESS.test(item)) ? list : null;
  };
  const to = decodeStrict(rawTo);
  const toList = to === null ? null : addresses(to);
  if (!toList) return null;
  const seen = new Set();
  for (const pair of rest.length ? rest[0].split("&") : []) {
    if (!pair) continue;
    const [rawKey, ...rawValue] = pair.split("=");
    const key = decodeStrict(rawKey)?.toLowerCase();
    if (!key || !MAILTO_PARAMS.has(key)) continue;
    if (seen.has(key)) return null;
    seen.add(key);
    const value = decodeStrict(rawValue.join("="));
    if (value === null) return null;
    if (key === "cc" || key === "bcc") {
      const list = addresses(value);
      if (!list) return null;
      parts.push(`${key}=${list.map(encodeURIComponent).join(",")}`);
    } else parts.push(`${key}=${encodeURIComponent(value)}`);
  }
  const href = `mailto:${toList.map((item) => item.split("@").map(encodeURIComponent).join("@")).join(",")}`
    + (parts.length ? `?${parts.join("&")}` : "");
  return href.length <= MAX_MAILTO ? href : null;
}

/**
 * Classify a link target from the product UI: "app" stays in the window, "external" opens in the
 * default browser (with the normalised string), "deny" does nothing. External means an http(s) URL
 * on a public host without credentials, or a plain mailto: address; local services (loopback,
 * private, link-local, tailnet IPs, local-only names), other schemes and unparsable input are denied.
 */
export function classifyUiLink(target, connection) {
  if (typeof target !== "string" || target.length > MAX_URL || CONTROL.test(target)) return { kind: "deny" };
  if (isOwnedUiNavigation(target, connection)) return { kind: "app" };
  let url;
  try {
    url = new URL(target);
  } catch {
    return { kind: "deny" };
  }
  if (url.protocol === "mailto:") {
    if (target.length > MAX_MAILTO) return { kind: "deny" };
    const href = normaliseMailto(url);
    return href ? { kind: "external", url: href } : { kind: "deny" };
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return { kind: "deny" };
  if (url.username || url.password || !isPublicHost(url.hostname)) return { kind: "deny" };
  return { kind: "external", url: url.href };
}

/**
 * Electron has no popup blocker and window-open details carry no user activation, so any frame
 * (e.g. a sandboxed embed with allow-popups) could open the browser in a loop. Allow one external
 * open per interval per window, drop the rest and log the first drop of each burst.
 */
export function createExternalOpener({ connection, openExternal, now = Date.now, intervalMs = 2000, log = defaultLog }) {
  let last = Number.NEGATIVE_INFINITY;
  let logged = false;
  return (target) => {
    const link = classifyUiLink(target, connection);
    if (link.kind !== "external") return false;
    const time = now();
    if (time - last < intervalMs) {
      if (!logged) log({ event: "desktop.external_link.throttled", interval_ms: intervalMs });
      logged = true;
      return false;
    }
    last = time;
    logged = false;
    Promise.resolve().then(() => openExternal(link.url))
      .catch(() => log({ event: "desktop.external_link.failed" }));
    return true;
  };
}

function defaultLog(fields) {
  console.warn(JSON.stringify(fields));
}

/** Hand one classified external URL to the OS without throttling state; never throws. */
export function openUiLinkExternally(target, connection, openExternal) {
  return createExternalOpener({ connection, openExternal, log: () => {} })(target);
}

/**
 * Only top-level documents use this guard; embedded third-party frames keep the web head's CSP.
 * The window never leaves the owned origin and never opens a child window: target=_blank,
 * window.open() and middle-click on a public link (from any frame) go to the default browser,
 * classified and rate-limited; everything else is denied.
 */
export function guardUiNavigation(contents, connection, openExternal, options = {}) {
  const open = createExternalOpener({ connection, openExternal, ...options });
  contents.setWindowOpenHandler((details) => {
    open(details?.url);
    return { action: "deny" };
  });
  contents.on("will-navigate", (event) => {
    if (isOwnedUiNavigation(event.url, connection)) return;
    event.preventDefault();
    open(event.url);
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
