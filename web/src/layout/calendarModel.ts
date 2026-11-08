// What the footer calendar glyph shows, as pure functions (ADR 0056: the footer item is a generic contribution fed
// by the `calendar` capability, so nothing here knows which calendar plugin supplies the agenda).
//
// The glyph is a 2px draining ring with the minutes inside it ("Ledger"). The ring is anchored on the END of the
// block it counts: 12 o'clock is the moment the current meeting ends. Angles are degrees clockwise from 12.

export type Level = "calm" | "warn" | "bad";

export type AgendaMeeting = { title: string; start: string; end: string; conference_url?: string | null };
export type AgendaData = {
  timezone?: string | null;
  current: AgendaMeeting | null;
  next: AgendaMeeting | null;
  problem: string | null;
  fix?: string | null;
};

/** A gap between the end of the current meeting and the next start maps onto one turn of the ring over this span. */
export const GAP_SCALE_MINUTES = 60;
/** Free time is drawn against this many minutes of ring; more than this fills it. */
export const FREE_SCALE_MINUTES = 120;
/** The ring turns amber at this many minutes left, red at BAD_MINUTES. */
export const WARN_MINUTES = 5;
export const BAD_MINUTES = 1;
/** An overlapping meeting that starts this many minutes from now (or sooner) turns the diamond red. */
export const OVERLAP_RED_MINUTES = 5;
/** In free time, a dot at 12 says the next meeting is this close. */
export const SOON_MINUTES = 15;
/** Free time longer than this is not counted: the plain calendar glyph shows. */
export const FREE_WINDOW_MINUTES = 600;

/**
 * Where the next meeting's start stroke sits when `gapMinutes` pass between the current meeting's end (12 o'clock)
 * and that start: proportionally around the ring, 7 minutes = 7/60 of the circle. A gap of 0 (back to back) is at 12.
 * A negative gap is an overlap (the diamond, see `overlapAngle`) and a gap of 60 minutes or more has no stroke.
 */
export function gapAngle(gapMinutes: number): number | null {
  if (!Number.isFinite(gapMinutes) || gapMinutes < 0 || gapMinutes >= GAP_SCALE_MINUTES) return null;
  return (360 * gapMinutes) / GAP_SCALE_MINUTES;
}

/** Where an overlapping meeting that starts at `nextStart` sits on the ring of the meeting `start`..`end`. */
export function overlapAngle(start: number, end: number, nextStart: number): number {
  if (end <= start) return 0;
  return Math.min(360, Math.max(0, (360 * (nextStart - start)) / (end - start)));
}

/** Whole minutes left until `target`, rounded up so that "0 min" never shows while time remains. */
export const minutesUntil = (target: number, now: number): number => Math.ceil((target - now) / 60_000);

/** Amber at 5 minutes or less, red at 1 or less; the number and the ring always agree. */
export function levelFor(minutes: number): Level {
  return minutes <= BAD_MINUTES ? "bad" : minutes <= WARN_MINUTES ? "warn" : "calm";
}

/** An overlapping meeting is urgent when it starts within five minutes of now, or already has. */
export const overlapIsUrgent = (startsInMinutes: number): boolean => startsInMinutes <= OVERLAP_RED_MINUTES;

/** The number inside the ring: minutes below 60, then whole hours `1h`..`9h` (floor), then `>9h`. Two or three characters. */
export function ringLabel(minutes: number): string {
  const n = Math.max(0, Math.ceil(minutes));
  if (n < 60) return String(n);
  return n < 600 ? `${Math.floor(n / 60)}h` : ">9h";
}

/** Two characters set the size (11.5px); three drop one step so the cap still fits inside the ring. */
export function labelFontSize(text: string): number {
  return text.length > 2 ? 9 : 11.5;
}

/** "5 min", "1 h", "2 h 10 min". */
export function spanText(minutes: number): string {
  const n = Math.max(0, Math.round(minutes));
  if (n < 60) return `${n} min`;
  const rest = n % 60;
  return rest ? `${Math.floor(n / 60)} h ${rest} min` : `${Math.floor(n / 60)} h`;
}

export type Marker =
  /** The next meeting overlaps the current one and starts `startsIn` minutes from now. Red when urgent. */
  | { kind: "diamond"; angle: number; red: boolean; startsIn: number }
  /** The next meeting starts `gap` minutes after this one ends; 0 is back to back (at 12). */
  | { kind: "stroke"; angle: number; gap: number }
  /** In free time the next meeting is close. */
  | { kind: "dot"; angle: 0 };

export type CountingModel = {
  kind: "meeting" | "free";
  level: Level;
  minutes: number;
  text: string;
  /** meeting: the share of the block that has elapsed (the drained part); free: the share of ring still full. */
  fraction: number;
  marker: Marker | null;
  description: string;
};

export type GlyphModel =
  | { kind: "unknown"; reason: "not-connected" | "unavailable"; description: string }
  | { kind: "idle"; description: string }
  | CountingModel;

type Input = { agenda: AgendaData | undefined; failed: boolean; pending: boolean; now: number };

const at = (m: AgendaMeeting) => ({ start: Date.parse(m.start), end: Date.parse(m.end) });
const valid = (m: AgendaMeeting | null | undefined): m is AgendaMeeting => {
  if (!m) return false;
  const { start, end } = at(m);
  return Number.isFinite(start) && Number.isFinite(end) && end > start;
};

/** The state machine: unknown, idle, in a meeting (with its next-meeting marker) or in free time. */
export function glyphModel({ agenda, failed, pending, now }: Input): GlyphModel {
  if (pending && !agenda) return { kind: "idle", description: "Loading calendar" };
  if (failed || !agenda) {
    return { kind: "unknown", reason: "unavailable", description: "Calendar unavailable" };
  }
  if (agenda.problem) {
    return {
      kind: "unknown",
      reason: agenda.fix ? "not-connected" : "unavailable",
      description: agenda.fix ? "Calendar not connected" : "Calendar unavailable",
    };
  }
  const running = [agenda.current, agenda.next]
    .filter(valid)
    .filter((m) => at(m).start <= now && now < at(m).end)
    .sort((a, b) => at(b).start - at(a).start);
  const active = running[0] ?? null;
  const upcoming = valid(agenda.next) && at(agenda.next).start > now ? agenda.next : null;

  if (active) {
    const { start, end } = at(active);
    const minutes = Math.max(1, minutesUntil(end, now));
    const level = levelFor(minutes);
    let marker: Marker | null = null;
    let clause = "";
    if (upcoming) {
      const nextStart = at(upcoming).start;
      const gap = (nextStart - end) / 60_000;
      if (gap < 0) {
        const startsIn = minutesUntil(nextStart, now);
        marker = {
          kind: "diamond",
          angle: overlapAngle(start, end, nextStart),
          red: overlapIsUrgent(startsIn),
          startsIn,
        };
        clause = `; overlapping meeting starts in ${startsIn} min`;
      } else if (gap === 0) {
        marker = { kind: "stroke", angle: 0, gap: 0 };
        clause = `; ${upcoming.title} starts right after, back to back`;
      } else {
        const angle = gapAngle(gap);
        if (angle !== null) {
          marker = { kind: "stroke", angle, gap };
          clause = `; ${upcoming.title} starts ${spanText(gap)} after it ends`;
        }
      }
    }
    return {
      kind: "meeting",
      level,
      minutes,
      text: ringLabel(minutes),
      fraction: Math.min(1, Math.max(0, (now - start) / (end - start))),
      marker,
      description: `In ${active.title}, ${spanText(minutes)} left${clause}`,
    };
  }

  if (upcoming) {
    const minutes = Math.max(1, minutesUntil(at(upcoming).start, now));
    if (minutes < FREE_WINDOW_MINUTES) {
      return {
        kind: "free",
        level: levelFor(minutes),
        minutes,
        text: ringLabel(minutes),
        fraction: Math.min(minutes, FREE_SCALE_MINUTES) / FREE_SCALE_MINUTES,
        marker: minutes <= SOON_MINUTES ? { kind: "dot", angle: 0 } : null,
        description: `Free for ${spanText(minutes)} until ${upcoming.title}`,
      };
    }
  }
  return { kind: "idle", description: "No meeting coming up" };
}
