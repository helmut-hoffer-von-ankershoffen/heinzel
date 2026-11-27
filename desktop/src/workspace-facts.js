import http from "node:http";
import https from "node:https";
import { isIP } from "node:net";
import { WORKSPACE_WORLD } from "./workspace-attachment.js";

export function versionFacts(payload, clientAddress) {
  const clean = (version) => typeof version === "string" && /^[\w.+-]{1,80}$/.test(version) ? version : null;
  const ip = (address) => typeof address === "string" && isIP(address) && !["0.0.0.0", "::"].includes(address) ? address : null;
  const version = clean(payload?.web?.version);
  const installed = clean(payload?.installed?.version);
  const start = Date.parse(payload?.web?.running_since);
  const end = Date.parse(payload?.observed_at);
  return { version, installedVersion: version && installed !== version ? installed : null,
    ipAddress: ip(payload?.web?.backend_ip), clientAddress: ip(clientAddress),
    uptimeSeconds: Number.isFinite(start) && Number.isFinite(end) && end >= start ? Math.floor((end - start) / 1000) : null };
}

// A storage event excludes the window that made the write. Sample only local storage state while
// Information is open so both the web client's own writes and changes from its other frames are visible.
function authorityExpression(origin) {
  return `location.origin === ${JSON.stringify(origin)} ? { launch: sessionStorage.getItem("hz.token"), device: localStorage.getItem("hz.device") } : null`;
}

export function watchWorkspaceAuthority(target, contents, signal, changed, interval = 350) {
  let previous, timer;
  const code = `(async () => {
    const authority = ${authorityExpression(target.origin)};
    if (!authority?.launch && !authority?.device) return null;
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(authority)));
    return Array.from(new Uint8Array(bytes)).join(".");
  })()`;
  async function check() {
    if (signal.aborted || contents.isDestroyed()) return;
    let fingerprint = null;
    try {
      fingerprint = await contents.executeJavaScriptInIsolatedWorld(WORKSPACE_WORLD, [{ code }]);
    } catch { /* An unavailable document cannot authenticate previously displayed facts. */ }
    if (signal.aborted || contents.isDestroyed()) return;
    if (previous !== fingerprint) {
      previous = fingerprint;
      changed();
    }
    if (!signal.aborted) timer = setTimeout(check, interval);
  }
  signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
  void check();
}

/** Read the selected document's current authority. Never keep it in picker state or send it to the chooser. */
export async function readWorkspaceFacts(target, contents, signal) {
  const frame = contents.mainFrame;
  const route = frame.url;
  if (new URL(route).origin !== target.origin) return {};
  const script = authorityExpression(target.origin);
  const current = () => !signal.aborted && !contents.isDestroyed() && contents.mainFrame === frame && frame.url === route;
  const authority = () => contents.executeJavaScriptInIsolatedWorld(WORKSPACE_WORLD, [{ code: script }]);
  if (!current()) return {};
  const direct = await contents.session.resolveProxy(target.origin) === "DIRECT";
  if (!current()) return {};
  const original = await authority();
  const first = original?.launch || original?.device;
  if (!current() || !first) return {};
  const unchanged = async () => {
    if (!current()) return false;
    const latest = await authority();
    return current() && latest?.launch === original.launch && latest?.device === original.device;
  };
  const url = new URL("/api/workspace-version", target.origin);
  const read = (token) => requestFacts(url, contents.session, direct, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" }, signal,
  });
  let response = await read(first);
  if (!await unchanged()) return {};
  if (response.status === 401 && original.device && original.device !== first) {
    response = await read(original.device);
    if (!await unchanged()) return {};
  }
  return response.facts ?? {};
}

/** The local socket belongs to the authenticated request. A proxy's peer address cannot identify the backend. */
async function requestFacts(url, session, direct, options) {
  if (!direct) {
    const response = await session.fetch(url.href, { ...options, redirect: "error", credentials: "omit" });
    if (!response.ok) return { status: response.status };
    return { status: response.status, facts: versionFacts(await response.json(), null) };
  }
  return new Promise((resolve, reject) => {
    const transport = url.protocol === "https:" ? https : http;
    transport.get(url, options, (response) => {
      if (response.statusCode !== 200) { response.destroy(); resolve({ status: response.statusCode }); return; }
      const localAddress = response.socket.localAddress;
      let body = "";
      response.setEncoding("utf8");
      response.on("error", reject);
      response.on("data", (chunk) => {
        body += chunk;
        if (body.length > 65536) response.destroy(new Error("Workspace information was too large."));
      });
      response.on("end", () => {
        try { resolve({ status: response.statusCode, facts: versionFacts(JSON.parse(body), localAddress) }); } catch (error) { reject(error); }
      });
    }).on("error", reject);
  });
}
