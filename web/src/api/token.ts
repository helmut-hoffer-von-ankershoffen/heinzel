// The session token `hz ui` opens the app with (ADR 0039): it arrives in the URL fragment, which never reaches a
// server, and is kept in sessionStorage for this tab only. No cookie: another site can't make the browser send it.
const KEY = "hz.token";

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

export function token(): string | null {
  return sessionStorage.getItem(KEY);
}
