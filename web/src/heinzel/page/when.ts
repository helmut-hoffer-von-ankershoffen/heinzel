// A time on the Heinzel's page in words a person reads at a glance: "Thu 14:00", or the date when it is further away.
const WEEK = 6 * 24 * 60 * 60 * 1000;

export function when(iso: string | null | undefined, now: Date = new Date()): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const near = Math.abs(date.getTime() - now.getTime()) < WEEK;
  return new Intl.DateTimeFormat(undefined, {
    ...(near ? { weekday: "short" } : { day: "numeric", month: "short" }),
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
