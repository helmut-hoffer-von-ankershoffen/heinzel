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
