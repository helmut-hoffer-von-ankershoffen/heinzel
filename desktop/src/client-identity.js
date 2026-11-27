// Which app this is, told to the workspace it connects to: it lists its connected clients (flavor, versions, machine).
import { hostname } from "node:os";

export const CLIENT_HEADER = "X-Heinzel-Client";

/** `flavor=electron;app=0.9.54;backend=0.9.54;machine=Air`: the same text the native app puts in its workspace API header. */
export function clientHeader({ appVersion, backendVersion, machine }) {
  const text = (value) => encodeURIComponent(Array.from(String(value ?? "unknown")).slice(0, 64).join("") || "unknown");
  return `flavor=electron;app=${text(appVersion)};backend=${text(backendVersion)};machine=${text(machine)}`;
}

/** Add the header to this workspace's requests, and only to them; `identity()` is read per request. */
export function attachClientHeader(workspaceSession, origin, identity) {
  workspaceSession.webRequest.onBeforeSendHeaders({ urls: ["<all_urls>"] }, (details, callback) => {
    const headers = { ...details.requestHeaders };
    for (const key of Object.keys(headers)) if (key.toLowerCase() === CLIENT_HEADER.toLowerCase()) delete headers[key];
    const url = new URL(details.url);
    if (url.origin === origin && url.pathname.startsWith("/api/") &&
        Object.keys(headers).some((key) => key.toLowerCase() === "authorization"))
      headers[CLIENT_HEADER] = clientHeader(identity());
    callback({ requestHeaders: headers });
  });
}

/** The machine's name for the list; `os.hostname()` is available without any permission. */
export const machineName = () => hostname().replace(/\.local$/, "");
