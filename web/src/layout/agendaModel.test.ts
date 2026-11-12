import { expect, it } from "vitest";
import {
  type AgendaEvent,
  addDays,
  clock,
  dateLabel,
  dayKey,
  groupWeek,
  nextEvent,
  nowIndex,
  rowState,
  rowTimes,
  weekCaption,
} from "./agendaModel";

const BERLIN = "Europe/Berlin";
// Sun 2026-11-08 08:18 in Europe/Berlin (UTC+1).
const NOW = Date.parse("2026-11-08T07:18:00Z");
const week = { since: "2026-11-01T23:00:00Z", until: "2026-11-08T23:00:00Z" };
const event = (id: string, start: string, end: string, extra: Partial<AgendaEvent> = {}): AgendaEvent => ({
  id,
  title: id,
  start,
  end,
  all_day: false,
  ...extra,
});
const group = (events: AgendaEvent[], over: Partial<Parameters<typeof groupWeek>[0]> = {}) =>
  groupWeek({ events, ...week, now: NOW, tz: BERLIN, ...over });

it("groups a Saturday event under Yesterday and a Sunday event under Today, with the date said once per day", () => {
  const days = group([
    event("sat", "2026-11-07T08:00:00Z", "2026-11-07T08:45:00Z"),
    event("sun", "2026-11-08T09:00:00Z", "2026-11-08T09:45:00Z"),
  ]);
  expect(days.map((day) => [day.date, day.relation, day.tense, day.events.length])).toEqual([
    ["Sat 7 Nov", "yesterday", "past", 1],
    ["Sun 8 Nov", "today", "today", 1],
  ]);
});

it("keeps an empty Today and drops every other empty day", () => {
  const days = group([event("tue", "2026-11-03T09:00:00Z", "2026-11-03T10:00:00Z")]);
  expect(days.map((day) => day.key)).toEqual(["2026-11-03", "2026-11-08"]);
  expect(days[1]).toMatchObject({ relation: "today", events: [] });
  expect(group([]).map((day) => day.key)).toEqual(["2026-11-08"]);
});

it("has no Today group in another week and captions it with the year", () => {
  const next = { since: "2026-11-08T23:00:00Z", until: "2026-11-15T23:00:00Z" };
  expect(group([], next)).toEqual([]);
  expect(weekCaption(next.since, next.until, BERLIN)).toBe("9–15 Nov 2026");
  expect(weekCaption(week.since, week.until, BERLIN)).toBe("2–8 Nov 2026");
  expect(weekCaption("2026-10-26T23:00:00Z", "2026-11-02T23:00:00Z", BERLIN)).toBe("27 Oct–2 Nov 2026");
  expect(weekCaption("2026-12-27T23:00:00Z", "2027-01-03T23:00:00Z", BERLIN)).toBe("28 Dec 2026–3 Jan 2027");
});

it("labels tomorrow and later days, and moves day keys by whole days across month ends", () => {
  const days = group(
    [
      event("mon", "2026-11-09T09:00:00Z", "2026-11-09T10:00:00Z"),
      event("tue", "2026-11-10T13:00:00Z", "2026-11-10T14:00:00Z"),
    ],
    { until: "2026-11-15T23:00:00Z" },
  );
  expect(days.map((day) => [day.date, day.relation, day.tense])).toEqual([
    ["Sun 8 Nov", "today", "today"],
    ["Mon 9 Nov", "tomorrow", "future"],
    ["Tue 10 Nov", null, "future"],
  ]);
  expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
  expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  expect(dateLabel("2026-11-08")).toBe("Sun 8 Nov");
});

it("follows the API time zone at midnight: 23:30Z is Sunday in Berlin and Saturday in UTC", () => {
  const late = event("late", "2026-11-07T23:30:00Z", "2026-11-08T00:15:00Z");
  expect(dayKey(late.start, BERLIN)).toBe("2026-11-08");
  expect(dayKey(late.start, "UTC")).toBe("2026-11-07");
  expect(group([late]).map((day) => [day.key, day.events.length])).toEqual([["2026-11-08", 1]]);
  const utc = group([late], { tz: "UTC", since: "2026-11-02T00:00:00Z", until: "2026-11-09T00:00:00Z" });
  expect(utc.map((day) => [day.key, day.events.map((e) => e.edge)])).toEqual([
    ["2026-11-07", ["start"]],
    ["2026-11-08", ["end"]],
  ]);
  expect(clock(late.start, BERLIN)).toBe("00:30");
  expect(clock(late.start, "UTC")).toBe("23:30");
});

it("places a multi-day event on every day it covers and says so with arrows", () => {
  const long = event("long", "2026-11-06T17:00:00Z", "2026-11-08T09:00:00Z");
  const days = group([long]);
  expect(days.map((day) => [day.key, day.events.map((e) => e.edge)])).toEqual([
    ["2026-11-06", ["start"]],
    ["2026-11-07", ["middle"]],
    ["2026-11-08", ["end"]],
  ]);
  expect(days.map((day) => rowTimes(day.events[0] as never, BERLIN))).toEqual([
    { allDay: false, top: "18:00 →", bottom: null },
    { allDay: true, top: null, bottom: null },
    { allDay: false, top: "→ 10:00", bottom: null },
  ]);
});

it("shows All day for all-day events, which come first and are neither past nor running", () => {
  const allDay = event("market", "2026-11-07T23:00:00Z", "2026-11-08T23:00:00Z", { all_day: true });
  const today = group([event("standup", "2026-11-08T07:00:00Z", "2026-11-08T07:30:00Z"), allDay]).at(-1);
  expect(today?.events.map((e) => e.event.id)).toEqual(["market", "standup"]);
  expect(rowTimes(today?.events[0] as never, BERLIN).allDay).toBe(true);
  expect(rowState(allDay, NOW).state).toBe("allday");
  expect(nowIndex(today?.events ?? [], NOW)).toBe(2);
});

it("derives running, past and upcoming state with the same arithmetic as the glyph", () => {
  const standup = event("standup", "2026-11-08T07:00:00Z", "2026-11-08T07:30:00Z");
  const review = event("review", "2026-11-08T09:00:00Z", "2026-11-08T09:45:00Z");
  const done = event("done", "2026-11-08T06:00:00Z", "2026-11-08T06:30:00Z");
  expect(rowState(standup, NOW)).toMatchObject({ state: "running", fraction: 0.6, minutesLeft: 12 });
  expect(rowState(review, NOW)).toMatchObject({ state: "upcoming", startsIn: 102 });
  expect(rowState(done, NOW).state).toBe("past");
  expect(rowState(standup, Date.parse(standup.end)).state).toBe("past");
});

it("puts the now line after what started and before the next event", () => {
  const events =
    group([
      event("done", "2026-11-08T06:00:00Z", "2026-11-08T06:30:00Z"),
      event("standup", "2026-11-08T07:00:00Z", "2026-11-08T07:30:00Z"),
      event("review", "2026-11-08T09:00:00Z", "2026-11-08T09:45:00Z"),
      event("later", "2026-11-08T13:00:00Z", "2026-11-08T14:00:00Z"),
    ])[0]?.events ?? [];
  expect(nowIndex(events, NOW)).toBe(2);
  expect(nextEvent(events, NOW)?.event.id).toBe("review");
  expect(nowIndex(events, Date.parse("2026-11-08T05:00:00Z"))).toBe(0);
  expect(nowIndex(events, Date.parse("2026-11-08T20:00:00Z"))).toBe(4);
  expect(nextEvent(events, Date.parse("2026-11-08T20:00:00Z"))).toBeUndefined();
});

it("rowTimes gives start over end and nothing that looks like a date", () => {
  const times = rowTimes({ event: event("a", "2026-11-08T08:00:00Z", "2026-11-08T08:45:00Z"), edge: "only" }, BERLIN);
  expect(times).toEqual({ allDay: false, top: "09:00", bottom: "09:45" });
});
