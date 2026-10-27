// The same working-day text for the clock pane, ticker and globe: real zoned instants, including weekends and DST.
import { t } from "@/i18n";
import { zoneHour, zoneTime } from "./zoneTime";

type Zone = { zone: string; hours: [number, number] };
type Wall = { year: number; month: number; day: number; hour: number; minute: number; second: number };
export type WorkingHours = {
  working: boolean;
  state: "working" | "off-duty" | "sleeping" | "unavailable";
  summary: string;
  detail: string;
  start?: Date;
  end?: Date;
  nextStart?: Date;
  previousEnd?: Date;
};
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

function stamp(wall: Wall): number {
  return Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute, wall.second);
}
function wallAt(format: Intl.DateTimeFormat, date: Date): Wall {
  const parts = Object.fromEntries(format.formatToParts(date).map((part) => [part.type, part.value]));
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    second: Number(parts.second),
  };
}

/** Resolve local calendar time with offsets on both sides of a possible transition, never by adding 24 real hours. */
function instant(format: Intl.DateTimeFormat, wall: Wall, closing: boolean): Date {
  const target = stamp(wall);
  const offsets = [
    ...new Set(
      [-36, 0, 36].map((hours) => {
        const at = target + hours * HOUR;
        return stamp(wallAt(format, new Date(at))) - at;
      }),
    ),
  ];
  const choices = offsets.map((offset) => target - offset);
  const exact = choices.filter((at) => stamp(wallAt(format, new Date(at))) === target).sort((a, b) => a - b);
  const selected = closing ? exact.at(-1) : exact[0];
  if (selected !== undefined) return new Date(selected);
  // A skipped wall time starts after the clock gap; a repeated one opens first and closes last.
  const after = choices.filter((at) => stamp(wallAt(format, new Date(at))) > target).sort((a, b) => a - b);
  return new Date(after[0] ?? choices[0] ?? target);
}

function duration(milliseconds: number, remaining = false): string {
  const total = Math.max(0, remaining ? Math.ceil(milliseconds / MINUTE) : Math.floor(milliseconds / MINUTE));
  const days = Math.floor(total / 1440),
    hours = Math.floor((total % 1440) / 60),
    minutes = total % 60;
  return [days ? `${days}d` : "", hours ? `${hours}h` : "", minutes || (!days && !hours) ? `${minutes}m` : ""]
    .filter(Boolean)
    .join(" ");
}

export function workingHours(zone: Zone, now: Date): WorkingHours {
  const unknown = {
    working: false,
    state: "unavailable" as const,
    summary: t("work.unavailable"),
    detail: t("work.unavailable"),
  };
  const [from, until] = zone.hours;
  if (
    !Number.isFinite(now.getTime()) ||
    !Number.isInteger(from) ||
    !Number.isInteger(until) ||
    from < 0 ||
    from >= until ||
    until > 24
  )
    return unknown;
  let format: Intl.DateTimeFormat;
  try {
    format = new Intl.DateTimeFormat("en-GB", {
      timeZone: zone.zone,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
      hourCycle: "h23",
    });
  } catch {
    return unknown;
  }
  const current = wallAt(format, now);
  const windows: { start: Date; end: Date }[] = [];
  for (let offset = -3; offset <= 3; offset++) {
    const day = new Date(Date.UTC(current.year, current.month - 1, current.day + offset));
    if (day.getUTCDay() === 0 || day.getUTCDay() === 6) continue;
    const wall = {
      year: day.getUTCFullYear(),
      month: day.getUTCMonth() + 1,
      day: day.getUTCDate(),
      minute: 0,
      second: 0,
    };
    windows.push({
      start: instant(format, { ...wall, hour: from }, false),
      end: instant(format, { ...wall, hour: until }, true),
    });
  }
  const active = windows.find((window) => now >= window.start && now < window.end);
  const next = windows.find((window) => window.start > now);
  const previous = windows.filter((window) => window.end <= now).at(-1);
  const absolute = (date: Date) =>
    `${new Intl.DateTimeFormat("en-GB", { timeZone: zone.zone, weekday: "short" }).format(date)} ${zoneTime(date, zone.zone)}`;
  const hours = t("work.hours", { from: zoneHour(from), until: zoneHour(until) });
  if (active)
    return {
      working: true,
      state: "working",
      start: active.start,
      end: active.end,
      summary: t("work.active", {
        elapsed: duration(now.getTime() - active.start.getTime()),
        remaining: duration(active.end.getTime() - now.getTime(), true),
      }),
      detail: `${hours} · ${t("work.ends", { at: absolute(active.end) })}`,
    };
  return {
    working: false,
    state: current.hour < 6 || current.hour >= 22 ? "sleeping" : "off-duty",
    nextStart: next?.start,
    previousEnd: previous?.end,
    summary: [
      next ? t("work.startsIn", { remaining: duration(next.start.getTime() - now.getTime(), true) }) : "",
      previous ? t("work.sinceEnd", { elapsed: duration(now.getTime() - previous.end.getTime()) }) : "",
    ]
      .filter(Boolean)
      .join(" · "),
    detail: [hours, next ? t("work.starts", { at: absolute(next.start) }) : ""].filter(Boolean).join(" · "),
  };
}
