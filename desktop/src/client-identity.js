// Which app this is, told to the workspace it connects to: it lists its connected clients (flavor, versions, machine).
import { hostname } from "node:os";

export const CLIENT_HEADER = "X-Heinzel-Client";

/** `flavor=electron;app=0.9.54;backend=0.9.54;machine=Air`: the same text the native app puts in its user agent. */
export function clientHeader({ appVersion, backendVersion, machine }) {
  const text = (value) => encodeURIComponent(String(value ?? "unknown").slice(0, 64) || "unknown");
  return `flavor=electron;app=${text(appVersion)};backend=${text(backendVersion)};machine=${text(machine)}`;
}

/** Add the header to this workspace's requests, and only to them; `identity()` is read per request. */
export function attachClientHeader(workspaceSession, origin, identity) {
  workspaceSession.webRequest.onBeforeSendHeaders({ urls: [`${origin}/*`] }, (details, callback) => {
    callback({ requestHeaders: { ...details.requestHeaders, [CLIENT_HEADER]: clientHeader(identity()) } });
  });
}

/** The machine's name for the list; `os.hostname()` is available without any permission. */
export const machineName = () => hostname().replace(/\.local$/, "");
