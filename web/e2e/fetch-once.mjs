// Playwright's own requests reuse idle connections and do not retry. The server closes a connection idle for 5 s, so a
// request sent at that instant fails with ECONNRESET. A browser retries this; these clients must not reuse at all.
export const ONE_CONNECTION = { connection: "close" };

/** Fetch what a route intercepted, on a connection of its own. */
export function fetchOnce(route) {
  return route.fetch({ headers: { ...route.request().headers(), ...ONE_CONNECTION } });
}
