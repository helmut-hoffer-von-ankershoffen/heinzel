// The footer calendar pane, read-only (phase 1): Now, Next, Then, a Join button and Open calendar. The write
// actions (decline, extend, running-late note) are phase 2 and stay disabled until the connector grants write
// access; what the calendar does not say stays unsaid (no attendee count, organizer or teaser is invented).

import { useQuery } from "@tanstack/react-query";
import { MessageSquareText, PanelRight, TimerReset, UserX, Video } from "lucide-react";
import { Link } from "react-router";
import { token } from "@/api/token";
import { conferenceLink } from "@/lib/conferenceLink";
import { cn } from "@/lib/utils";
import { type GlyphModel, spanText } from "./calendarModel";
import { CONNECTORS } from "./settingsSections";

export type SummaryEvent = {
  id?: string;
  title: string;
  start: string;
  end: string;
  all_day?: boolean;
  conference_url?: string | null;
  location?: string | null;
  organizer?: { name?: string | null; email?: string | null } | null;
  attendees?: unknown[] | null;
};

const LEVEL = { calm: "text-foreground", warn: "text-warn", bad: "text-bad" } as const;

/** A few lines after the next meeting; reads the week the sidebar calendar already reads (shared cache key). */
function Then({ after, now, zone }: { after: SummaryEvent; now: number; zone?: string | null }) {
  const week = useQuery({
    queryKey: ["org", "calendar", 0],
    queryFn: async () => {
      const url = new URL("/api/org/calendar", window.location.origin);
      url.searchParams.set("offset", "0");
      const response = await fetch(new Request(url, { headers: { Authorization: `Bearer ${token() ?? ""}` } }));
      if (!response.ok) throw new Error("Calendar unavailable");
      return (await response.json()) as { events: SummaryEvent[] };
    },
    retry: false,
  });
  const later = (week.data?.events ?? [])
    .filter(
      (event) =>
        !event.all_day &&
        Date.parse(event.start) > Date.parse(after.start) &&
        Date.parse(event.start) < now + 24 * 3_600_000 &&
        (event.id === undefined || event.id !== after.id),
    )
    .slice(0, 12);
  if (later.length === 0) return null;
  const clock = (iso: string) =>
    new Intl.DateTimeFormat(undefined, { timeStyle: "short", hourCycle: "h23", timeZone: zone ?? undefined }).format(
      new Date(iso),
    );
  return (
    <section aria-label="Then" className="space-y-1.5">
      <p className="text-[13px] font-medium tracking-wide text-muted-foreground uppercase">Then</p>
      <ul className="max-h-36 space-y-1 overflow-y-auto pr-1 text-[13px]">
        {later.map((event) => {
          const overlaps = Date.parse(event.start) < Date.parse(after.end);
          return (
            <li key={`${event.id ?? event.title}:${event.start}`} className="flex gap-2">
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {clock(event.start)}–{clock(event.end)}
              </span>
              <span className="min-w-0 break-words">
                {event.title || "(no title)"}
                {overlaps && <span className="text-muted-foreground"> · overlaps</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function details(event: SummaryEvent): string[] {
  const count = event.attendees?.length ?? 0;
  const who = event.organizer?.name?.trim();
  return [...(who ? [`${who} organizes`] : []), ...(count > 0 ? [`${count} invited`] : [])];
}

export function CalendarSummary({
  model,
  active,
  upcoming,
  now,
  zone,
  clock,
  onOpen,
}: {
  model: GlyphModel;
  active: SummaryEvent | null;
  upcoming: SummaryEvent | null;
  now: number;
  zone?: string | null;
  clock: (iso: string) => string;
  onOpen: () => void;
}) {
  const level = model.kind === "meeting" || model.kind === "free" ? model.level : "calm";
  const join = conferenceLink((active ?? upcoming)?.conference_url);
  const joinTitle = (active ?? upcoming)?.title ?? "";
  const startsIn = upcoming ? Math.max(1, Math.ceil((Date.parse(upcoming.start) - now) / 60_000)) : null;
  return (
    <div className="space-y-3.5 p-3 text-[14px] leading-snug">
      <p className="text-[17px] font-semibold">Calendar</p>
      <p className="sr-only" role="status">
        {model.description}
      </p>
      <section aria-label="Now" className="space-y-1 rounded-lg bg-accent/50 p-3.5">
        <p className="text-[13px] font-medium tracking-wide text-muted-foreground uppercase">Now</p>
        {active ? (
          <>
            <p className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="text-[17px] font-semibold break-words">In {active.title || "(no title)"}</span>
              <span className={cn("font-medium tabular-nums", LEVEL[level])}>
                {model.kind === "meeting" ? `${spanText(model.minutes)} left` : ""}
              </span>
            </p>
            <p className="text-[13px] text-muted-foreground">
              <time dateTime={active.start}>{clock(active.start)}</time>–
              <time dateTime={active.end}>{clock(active.end)}</time>
              {details(active).map((part) => ` · ${part}`)}
            </p>
            {active.location && <p className="text-[13px] break-words">{active.location}</p>}
          </>
        ) : (
          <>
            <p className="text-[17px] font-semibold">Not in a meeting</p>
            {model.kind === "free" && (
              <p className={cn("text-[13px]", LEVEL[level])}>Free for {spanText(model.minutes)}</p>
            )}
          </>
        )}
      </section>
      <section aria-label="Next" className="space-y-1">
        <p className="text-[13px] font-medium tracking-wide text-muted-foreground uppercase">Next</p>
        {upcoming && startsIn !== null ? (
          <>
            <p className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="font-semibold break-words">Next: {upcoming.title || "(no title)"}</span>
              <span className="tabular-nums text-muted-foreground">in {startsIn} min</span>
            </p>
            <p className="text-[13px] text-muted-foreground">
              <time dateTime={upcoming.start}>{clock(upcoming.start)}</time>–
              <time dateTime={upcoming.end}>{clock(upcoming.end)}</time>
              {details(upcoming).map((part) => ` · ${part}`)}
            </p>
            {upcoming.location && <p className="text-[13px] break-words">{upcoming.location}</p>}
          </>
        ) : (
          <p>None through tomorrow</p>
        )}
      </section>
      {upcoming && <Then after={upcoming} now={now} zone={zone} />}
      <div className="flex items-center gap-1 border-t border-border pt-3" role="toolbar" aria-label="Calendar actions">
        {join ? (
          <a
            href={join}
            target="_blank"
            rel="noreferrer noopener"
            aria-label={`Join ${joinTitle}`}
            title={`Join ${joinTitle}`}
            className="flex size-11 items-center justify-center rounded-lg bg-primary text-primary-foreground hover:opacity-90"
          >
            <Video aria-hidden className="size-5" />
          </a>
        ) : null}
        {(
          [
            ["Decline", UserX],
            ["Extend", TimerReset],
            ["Tell the next meeting you are late", MessageSquareText],
          ] as const
        ).map(([name, Icon]) => (
          <button
            key={name}
            type="button"
            disabled
            aria-label={`${name} (needs permission to change your calendar)`}
            title="Needs permission to change your calendar"
            className="flex size-11 items-center justify-center rounded-lg text-muted-foreground disabled:opacity-50"
          >
            <Icon aria-hidden className="size-5" />
          </button>
        ))}
        <span className="flex-1" />
        <button
          type="button"
          aria-label="Open calendar"
          title="Open calendar"
          className="flex size-11 items-center justify-center rounded-lg hover:bg-accent"
          onClick={onOpen}
        >
          <PanelRight aria-hidden className="size-5" />
        </button>
      </div>
      <Link
        to={`?settings=${CONNECTORS}`}
        className="inline-flex min-h-11 items-center text-[13px] text-primary underline"
      >
        Allow your Heinzels to change your calendar
      </Link>
    </div>
  );
}
