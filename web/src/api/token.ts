// The session token `hz ui` opens the app with (ADR 0039): it arrives in the URL fragment, which never reaches a
// server, and is kept in sessionStorage for this tab only. No cookie: another site can't make the browser send it.
// A paired device's token (ADR 0046) lives in localStorage, so the installed app reopens from the Dock without a link;
// the session token, when there is one, comes first.
const KEY = "hz.token";
export const DEVICE_KEY = "hz.device";

/** Take the token from `#token=…` into sessionStorage, drop it from the address bar, and return it. */
export function adoptToken(location: Location = window.location, history: History = window.history): string | null {
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ""));
  const fresh = fragment.get("token");
  if (fresh) {
    sessionStorage.setItem(KEY, fresh);
    fragment.delete("token");
    const rest = fragment.toString();
    history.replaceState(history.state, "", `${location.pathname}${location.search}${rest ? `#${rest}` : ""}`);
  }
  return sessionStorage.getItem(KEY);
}

/** The session token of this tab; none in an app reopened from the Dock. */
export function sessionToken(): string | null {
  return sessionStorage.getItem(KEY);
}

/** This browser's device token, if it is paired. */
export function deviceToken(): string | null {
  try {
    return localStorage.getItem(DEVICE_KEY);
  } catch {
    return null;
  }
}

export function token(): string | null {
  return sessionToken() ?? deviceToken();
}
