// Days and times of a conversation, in one zone: the calendar's helpers (`dayKey`, `addDays`, `zoneTime`) with the
// words a chat needs. The zone is the person's (`ownZone()`, the browser's) unless the caller has the calendar's own.
// Slice 7 (the Heinzel's page) and slice 12 (the Done list) take their `when()` and day headings from here.
import { useEffect, useState } from "react";
import { t } from "@/i18n";
import { addDays, dayKey } from "@/layout/agendaModel";
import { ownZone } from "@/layout/settingSchema";
import { zoneClock, zoneTime } from "@/lib/zoneTime";

const longDay = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
const longDayYear = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});

/** `YYYY-MM-DD` of `at` in `zone`. */
export const dayOf = (at: string | number | Date, zone: string = ownZone()) => dayKey(at, zone);

/** "Thursday 13 November", with the year when `key` is not in `thisYear`. */
function dayWords(key: string, thisYear: string): string {
  const noon = new Date(`${key}T12:00:00Z`);
  const parts = (key.slice(0, 4) === thisYear ? longDay : longDayYear).formatToParts(noon);
  const pick = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return [pick("weekday"), pick("day"), pick("month"), key.slice(0, 4) === thisYear ? "" : pick("year")]
    .filter(Boolean)
    .join(" ");
}

/** "Today", "Yesterday", else "Thursday 13 November" (with the year when it is not this year), in `zone`. */
export function dayHeading(at: string, now: Date = new Date(), zone: string = ownZone()): string {
  const key = dayKey(at, zone);
  const today = dayKey(now, zone);
  if (key === today) return t("oneChat.today");
  if (key === addDays(today, -1)) return t("oneChat.yesterday");
  return dayWords(key, today.slice(0, 4));
}

/** 09:05 on the 24-hour clock in `zone`. */
export const timeOfDay = (at: string, zone: string = ownZone()) => zoneTime(new Date(at), zone);

/** "Friday 13 November 2026, 09:05": the whole moment, for the page text. */
export function fullMoment(at: string, zone: string = ownZone()): string {
  return `${dayWords(dayKey(at, zone), "")}, ${timeOfDay(at, zone)}`;
}

/** Indexes whose message opens a new day: the first dated one, then each change of day against the last dated one. */
export function dayStarts(ats: (string | undefined)[], zone: string = ownZone()): Set<number> {
  const starts = new Set<number>();
  let last: string | undefined;
  ats.forEach((at, index) => {
    if (!at) return;
    const key = dayKey(at, zone);
    if (key !== last) starts.add(index);
    last = key;
  });
  return starts;
}

const SECOND = 1000;

/** The current time, renewed just after each midnight in `zone`, so "Today" never outlives its day. */
export function useNow(zone: string = ownZone()): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const [h = 0, m = 0, s = 0] = zoneClock(now, zone).split(":").map(Number);
    const left = (24 * 3600 - (h * 3600 + m * 60 + s)) * SECOND;
    const timer = setTimeout(() => setNow(new Date()), left + SECOND);
    return () => clearTimeout(timer);
  }, [now, zone]);
  return now;
}
