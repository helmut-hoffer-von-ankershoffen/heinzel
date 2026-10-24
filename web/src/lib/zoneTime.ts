// A time as the people in a time zone write it: "11:04 am" in New York or Sydney, "17:04" in Berlin or Tokyo.
// Intl doesn't say which country a zone is in, so a table names the zones of countries that use the 12-hour clock;
// every other zone gets the 24-hour clock.

/** The locale of each 12-hour country, by the zone names (or prefixes ending in `/`) that belong to it. */
const TWELVE_HOUR: [string, string[]][] = [
  [
    "en-US",
    [
      "America/New_York",
      "America/Chicago",
      "America/Denver",
      "America/Los_Angeles",
      "America/Phoenix",
      "America/Anchorage",
      "America/Juneau",
      "America/Sitka",
      "America/Nome",
      "America/Adak",
      "America/Boise",
      "America/Detroit",
      "America/Menominee",
      "America/Indiana/",
      "America/Kentucky/",
      "America/North_Dakota/",
      "America/Puerto_Rico",
      "Pacific/Honolulu",
      "Pacific/Guam",
      "US/",
    ],
  ],
  [
    "en-CA",
    [
      "America/Toronto",
      "America/Vancouver",
      "America/Edmonton",
      "America/Winnipeg",
      "America/Halifax",
      "America/St_Johns",
      "America/Regina",
      "America/Moncton",
      "America/Whitehorse",
      "America/Yellowknife",
      "Canada/",
    ],
  ],
  ["en-AU", ["Australia/"]],
  ["en-NZ", ["Pacific/Auckland", "Pacific/Chatham", "NZ"]],
  ["en-IN", ["Asia/Kolkata", "Asia/Calcutta"]],
  ["en-PH", ["Asia/Manila"]],
  ["en-PK", ["Asia/Karachi"]],
  ["en-BD", ["Asia/Dhaka"]],
  ["en-EG", ["Africa/Cairo"]],
];

/** The locale whose 12-hour clock `zone` uses, or null for the 24-hour clock. */
export function twelveHourLocale(zone: string): string | null {
  for (const [locale, zones] of TWELVE_HOUR) {
    if (zones.some((name) => (name.endsWith("/") ? zone.startsWith(name) : zone === name))) return locale;
  }
  return null;
}

/** `date`'s time of day in `zone`, in that place's convention: "11:04 am" or "17:04". */
export function zoneTime(date: Date, zone: string): string {
  const locale = twelveHourLocale(zone);
  if (!locale) {
    return new Intl.DateTimeFormat("en-GB", { timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .format(date)
      .replace(/^24/, "00");
  }
  const parts = new Intl.DateTimeFormat(locale, {
    timeZone: zone,
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).formatToParts(date);
  const value = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("hour")}:${value("minute")} ${value("dayPeriod").replaceAll(".", "").toLowerCase()}`;
}

/** A whole hour (0–24) of the working day in `zone`'s convention: "6:00 pm" or "18:00". */
export function zoneHour(hour: number, zone: string): string {
  if (!twelveHourLocale(zone)) return `${String(hour).padStart(2, "0")}:00`;
  const h = hour % 24;
  return `${h % 12 || 12}:00 ${h < 12 ? "am" : "pm"}`;
}
