/** A panel-only destination opens in the current page, preserving its filters and anchor. */
export function contributionPath(path: string, location: { pathname: string; search: string; hash: string }): string {
  if (!path.startsWith("/?") || path.includes("#")) return path;
  const target = new URLSearchParams(path.slice(2));
  const panel = target.get("panel");
  if (!panel || target.size !== 1) return path;
  const query = new URLSearchParams(location.search);
  query.set("panel", panel);
  return `${location.pathname}?${query}${location.hash}`;
}

/** Query panel destinations are active only when that panel is selected. Other links keep route matching. */
export function contributionPanel(path: string): string | null {
  if (!path.startsWith("/?") || path.includes("#")) return null;
  const target = new URLSearchParams(path.slice(2));
  const panel = target.get("panel");
  return panel && target.size === 1 ? panel : null;
}

export function contributionIsActive(
  path: string,
  location: { pathname: string; search: string; hash: string },
  routeIsActive: boolean,
): boolean {
  const panel = contributionPanel(path);
  if (panel) return new URLSearchParams(location.search).get("panel") === panel;

  const destination = new URL(path, "http://local");
  if (!destination.search) return routeIsActive;
  const current = new URLSearchParams(location.search);
  return routeIsActive && [...destination.searchParams].every(([key, value]) => current.getAll(key).includes(value));
}
