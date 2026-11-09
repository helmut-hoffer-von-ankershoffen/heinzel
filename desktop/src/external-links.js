// Shared by the native IPC boundary and the web controls that offer these actions in the desktop app.
const meetingRoutes = [
  [/(^|\.)zoom\.(us|com)$/, /^\/(?:j\/\d+|my\/[\w.-]+|wc\/join\/\d+)\/?$/],
  [/^meet\.google\.com$/, /^\/(?:[a-z]{3}-[a-z]{4}-[a-z]{3}|lookup\/[\w-]+)\/?$/],
  [/^teams\.(microsoft|live)\.com$/, /^\/(?:l\/meetup-join\/[^/]+(?:\/\d+)?|meet\/\d+)\/?$/],
  [/(^|\.)webex\.com$/, /^\/(?:meet|join)\/[\w.-]+\/?$/],
];
const googleCalendar = /^\/(?:calendar\/(?:u\/\d+\/)?(?:r\/)?)(?:event|eventedit)(?:\/[\w=-]+)?\/?$/;

/** The system browser receives a canonical HTTPS URL for a specific product action. */
export function externalActionUrl(action, target) {
  if (typeof target !== "string" || target.length > 2048 || /[\s\\"'<>\x00-\x1f\x7f-\x9f]/u.test(target) ||
      !/^https:\/\//i.test(target)) return null;
  const url = URL.parse(target);
  if (!url || url.username || url.password || url.port) return null;
  const { hostname, pathname } = url;
  if (action === "watch-video") {
    const [video, extra] = url.searchParams.getAll("v");
    return hostname === "www.youtube.com" && pathname === "/watch" && !extra && /^[\w-]{11}$/.test(video ?? "")
      ? `https://www.youtube.com/watch?v=${video}` : null;
  }
  if (action === "join-meeting" && meetingRoutes.some(([host, path]) => host.test(hostname) && path.test(pathname)))
    return url.href;
  if (action === "open-calendar" && (hostname === "calendar.google.com" || hostname === "www.google.com") &&
      googleCalendar.test(pathname)) return url.href;
  return null;
}
