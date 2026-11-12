// The calendar pane's week as a day-grouped agenda, as pure functions (research/calendar-sidebar-2026-11-08.md).
// Days follow the API's time zone, never the browser's; a date is said once, in the day header, and rows carry times.
import { t } from "@/i18n";
import { minutesUntil } from "./calendarModel";

export type AgendaEvent = {
  id: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
};

/** Which part of a (possibly multi-day) event a day shows. */
export type Edge = "only" | "start" | "middle" | "end";
export type DayEvent<E extends AgendaEvent = AgendaEvent> = { event: E; edge: Edge };
export type Day<E extends AgendaEvent = AgendaEvent> = {
  /** `YYYY-MM-DD` in the API time zone. */
  key: string;
  /** "Sun 8 Nov". */
  date: string;
  relation: "yesterday" | "today" | "tomorrow" | null;
  tense: "past" | "today" | "future";
  events: DayEvent<E>[];
};

const formatters = new Map<string, Intl.DateTimeFormat>();
function formatter(kind: string, timeZone: string | null, options: Intl.DateTimeFormatOptions, locale = "en-GB") {
  const id = `${kind}|${locale}|${timeZone ?? ""}`;
  let known = formatters.get(id);
  if (!known) {
    known = new Intl.DateTimeFormat(locale, { ...options, timeZone: timeZone ?? undefined });
    formatters.set(id, known);
  }
  return known;
}

/** The calendar day of `moment` in `timeZone` as `YYYY-MM-DD`. */
export function dayKey(moment: string | number | Date, timeZone: string | null): string {
  const parts = formatter(
    "key",
    timeZone,
    { year: "numeric", month: "2-digit", day: "2-digit" },
    "en-CA",
  ).formatToParts(new Date(moment));
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

/** `key` moved by whole days (pure calendar arithmetic, no time zone involved). */
export function addDays(key: string, days: number): string {
  const [year = 0, month = 1, day = 1] = key.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

/** 09:00, in the API time zone. */
export function clock(moment: string | number | Date, timeZone: string | null): string {
  return formatter("clock", timeZone, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(
    new Date(moment),
  );
}

function parts(key: string) {
  const [year = 0, month = 1, day = 1] = key.split("-").map(Number);
  return { year, month, day, noon: new Date(Date.UTC(year, month - 1, day, 12)) };
}

const dayText = (key: string, options: Intl.DateTimeFormatOptions) =>
  formatter(`day${JSON.stringify(options)}`, "UTC", options).formatToParts(parts(key).noon);

/** "Sun 8 Nov". */
export function dateLabel(key: string): string {
  const p = dayText(key, { weekday: "short", day: "numeric", month: "short" });
  const part = (type: string) => p.find((x) => x.type === type)?.value ?? "";
  return `${part("weekday")} ${part("day")} ${part("month")}`;
}

/** "9–15 Nov 2026", "28 Oct–3 Nov 2026" or "28 Dec 2026–3 Jan 2027" for the week `since`..`until`. */
export function weekCaption(since: string, until: string, timeZone: string | null): string {
  const first = dayKey(since, timeZone);
  const last = dayKey(new Date(until).getTime() - 1, timeZone);
  const a = parts(first);
  const b = parts(last);
  const month = (key: string) => dayText(key, { month: "short" }).find((x) => x.type === "month")?.value ?? "";
  const from =
    a.year !== b.year
      ? `${a.day} ${month(first)} ${a.year}`
      : a.month !== b.month
        ? `${a.day} ${month(first)}`
        : String(a.day);
  return t("calendar.weekCaption", { from, to: `${b.day} ${month(last)} ${b.year}` });
}

/** Every day key from `since` to the instant before `until`. */
function weekKeys(since: string, until: string, timeZone: string | null): string[] {
  const keys: string[] = [];
  const last = dayKey(new Date(until).getTime() - 1, timeZone);
  for (let key = dayKey(since, timeZone); key <= last && keys.length < 400; key = addDays(key, 1)) keys.push(key);
  return keys;
}

const startOf = (event: AgendaEvent) => Date.parse(event.start);
const endOf = (event: AgendaEvent) => Date.parse(event.end);

/** Groups the week by day: Today is kept even when empty, every other day only when it has events. */
export function groupWeek<E extends AgendaEvent>({
  events,
  since,
  until,
  now,
  tz,
}: {
  events: E[];
  since: string;
  until: string;
  now: number;
  tz: string | null;
}): Day<E>[] {
  const today = dayKey(now, tz);
  const days: Day<E>[] = weekKeys(since, until, tz).map((key) => ({
    key,
    date: dateLabel(key),
    relation:
      key === today
        ? "today"
        : key === addDays(today, -1)
          ? "yesterday"
          : key === addDays(today, 1)
            ? "tomorrow"
            : null,
    tense: key < today ? "past" : key === today ? "today" : "future",
    events: [],
  }));
  for (const event of events) {
    const start = startOf(event);
    const end = endOf(event);
    if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
    const first = dayKey(start, tz);
    const last = dayKey(Math.max(start, end - 1), tz);
    for (const day of days) {
      if (day.key < first || day.key > last) continue;
      const edge: Edge = first === last ? "only" : day.key === first ? "start" : day.key === last ? "end" : "middle";
      day.events.push({ event, edge });
    }
  }
  // Whole-day rows first, then by start; the part of an event that continues from an earlier day leads the day.
  const rank = (e: DayEvent) => (e.event.all_day || e.edge === "middle" ? 0 : e.edge === "end" ? 1 : 2);
  for (const day of days) {
    day.events.sort((a, b) => rank(a) - rank(b) || (rank(a) === 2 ? startOf(a.event) - startOf(b.event) : 0));
  }
  return days.filter((day) => day.relation === "today" || day.events.length > 0);
}

export type RowTimes = { allDay: boolean; top: string | null; bottom: string | null };

/** The time column of a row: start over end, "All day", or an arrow on the days an event crosses midnight. */
export function rowTimes({ event, edge }: DayEvent, tz: string | null): RowTimes {
  if (event.all_day || edge === "middle") return { allDay: true, top: null, bottom: null };
  if (edge === "start")
    return { allDay: false, top: t("calendar.startsAt", { time: clock(event.start, tz) }), bottom: null };
  if (edge === "end") return { allDay: false, top: t("calendar.endsAt", { time: clock(event.end, tz) }), bottom: null };
  return { allDay: false, top: clock(event.start, tz), bottom: clock(event.end, tz) };
}

export type RowState = {
  state: "allday" | "past" | "running" | "upcoming";
  /** Elapsed share of a running event, 0..1. */
  fraction: number;
  minutesLeft: number;
  startsIn: number;
};

/** Where an event stands against `now`; all-day events are neither past nor running. */
export function rowState(event: AgendaEvent, now: number): RowState {
  const start = startOf(event);
  const end = endOf(event);
  const base = { fraction: 0, minutesLeft: 0, startsIn: 0 };
  if (event.all_day) return { ...base, state: "allday" };
  if (end <= now) return { ...base, state: "past" };
  if (start <= now) {
    return {
      state: "running",
      fraction: end > start ? Math.min(1, Math.max(0, (now - start) / (end - start))) : 0,
      minutesLeft: minutesUntil(end, now),
      startsIn: 0,
    };
  }
  return { ...base, state: "upcoming", startsIn: minutesUntil(start, now) };
}

/** The first timed event still to start: the one that gets "in 1 h 42 min". */
export function nextEvent<E extends AgendaEvent>(events: DayEvent<E>[], now: number): DayEvent<E> | undefined {
  return events.find((e) => !e.event.all_day && e.edge !== "middle" && e.edge !== "end" && startOf(e.event) > now);
}

/** How many rows precede the now line: whole-day rows and everything that started already. */
export function nowIndex(events: DayEvent[], now: number): number {
  const next = nextEvent(events, now);
  return next ? events.indexOf(next) : events.length;
}
