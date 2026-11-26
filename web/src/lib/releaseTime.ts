// When a release was done: day and time in the viewer's time zone with the zone shown ("22 Nov 2026, 08:41 CET");
// only the day when no moment is known (an older release, or offline).

const moment = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZoneName: "short",
});

const dayOnly = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/** The label for a release from the API's `published_at` (an instant) or `day` (a date); null if neither is known. */
export function releaseTime(publishedAt?: string | null, day?: string | null): string | null {
  if (publishedAt) {
    const at = new Date(publishedAt);
    if (!Number.isNaN(at.getTime())) return moment.format(at);
  }
  if (day) {
    const at = new Date(`${day}T00:00:00Z`);
    if (!Number.isNaN(at.getTime())) return dayOnly.format(at);
  }
  return null;
}
