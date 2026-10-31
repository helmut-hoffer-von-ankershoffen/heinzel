// Pairing (ADR 0046): with the session token `hz ui` opened the tab with, ask once for a device token and keep it,
// so this browser, or the app installed from it, opens without a new link. `hz ui --devices` lists the paired ones.
import { DEVICE_KEY, deviceToken, dropSessionToken, sessionToken } from "./token";

/** What the browser is, in words, for `hz ui --devices`: no fingerprint, just the browser, the system and the mode. */
export function deviceName(agent: string = navigator.userAgent): string {
  const browser = /Edg\//.test(agent)
    ? "Edge"
    : /Chrome\//.test(agent)
      ? "Chrome"
      : /Firefox\//.test(agent)
        ? "Firefox"
        : /Safari\//.test(agent)
          ? "Safari"
          : "A browser";
  const system = /Mac OS X|Macintosh/.test(agent) ? "macOS" : /Linux/.test(agent) ? "Linux" : "another system";
  const installed = window.matchMedia?.("(display-mode: standalone)").matches ? " (installed)" : "";
  return `${browser} on ${system}${installed}`;
}

/** Pair this browser if it isn't yet and this tab has a session token; whether it is paired afterwards. */
export async function pairDevice(fetcher: typeof fetch = (request) => globalThis.fetch(request)): Promise<boolean> {
  if (deviceToken()) return true;
  const session = sessionToken();
  if (!session) return false;
  try {
    const response = await fetcher(
      new Request(`${window.location.origin}/api/_web/device`, {
        method: "POST",
        headers: { Authorization: `Bearer ${session}`, "Content-Type": "application/json" },
        body: JSON.stringify({ name: deviceName() }),
      }),
    );
    if (!response.ok) return false;
    const { token } = (await response.json()) as { token?: string };
    if (!token) return false;
    localStorage.setItem(DEVICE_KEY, token);
    return true;
  } catch {
    return false; // storage blocked or `hz ui` gone: the session token still works in this tab
  }
}

/** Unpair this browser: the server forgets its token, and so does this browser. */
export async function forgetDevice(fetcher: typeof fetch = (request) => globalThis.fetch(request)): Promise<void> {
  const device = deviceToken();
  if (!device) return;
  try {
    await fetcher(
      new Request(`${window.location.origin}/api/_web/device`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${device}` },
      }),
    );
  } finally {
    localStorage.removeItem(DEVICE_KEY);
  }
}

/**
 * A token the server refused. A device token (unpaired with `hz ui --revoke`) is dropped, so the next link pairs again.
 * A session token is dead once `hz ui` started again with a new one; a paired browser then falls back to its device
 * token, which every later `hz ui` accepts (ADR 0046), instead of staying signed out behind a token that can't work.
 */
export function dropRefusedDevice(status: number, sent: string | null, reload: () => void = () => location.reload()) {
  if (status !== 401 || !sent) return;
  if (sent === sessionToken() && deviceToken()) {
    dropSessionToken();
    reload();
  } else if (sent === deviceToken() && !sessionToken()) localStorage.removeItem(DEVICE_KEY);
}

/**
 * Before the app asks for anything: a session token from an earlier `hz ui` (this window kept it across a restart, as an
 * installed app window does) is checked once, and dropped when refused, so a paired browser uses its device token.
 */
export async function dropStaleSession(fetcher: typeof fetch = (request) => globalThis.fetch(request)): Promise<void> {
  const session = sessionToken();
  if (!session || !deviceToken()) return;
  try {
    const response = await fetcher(
      new Request(`${window.location.origin}/api/system/ui/navigation`, {
        headers: { Authorization: `Bearer ${session}` },
      }),
    );
    if (response.status === 401) dropSessionToken();
  } catch {
    // hz isn't answering: the splash says so; keep the token for when it is back.
  }
}
