// Every time in the app is on the 24-hour clock ("08:44", "20:45"), whatever the locale or the place. This is the one
// formatter; screens never call toLocaleTimeString or ask Intl for a 12-hour clock.

const formatter = (zone: string | undefined, seconds: boolean) =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    hour: "2-digit",
    minute: "2-digit",
    second: seconds ? "2-digit" : undefined,
    hourCycle: "h23",
  });

/** `date`'s time of day as 17:04 in `zone` (this machine's by default). */
export function zoneTime(date: Date, zone?: string): string {
  return formatter(zone, false).format(date).replace(/^24/, "00");
}

/** `date`'s time of day as 17:04:09 in `zone` (this machine's by default). */
export function zoneClock(date: Date, zone?: string): string {
  return formatter(zone, true).format(date).replace(/^24/, "00");
}

/** A whole hour (0–24) of the working day: "09:00", "18:00". */
export function zoneHour(hour: number, _zone?: string): string {
  return `${String(hour).padStart(2, "0")}:00`;
}
