import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { token } from "@/api/token";
import { CONNECTORS } from "./settingsSections";

type Event = {
  id: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  conference_url?: string | null;
};
type Week = {
  timezone: string | null;
  since: string;
  until: string;
  events: Event[];
  problem: string | null;
  fix: string | null;
};
type ReminderStatus = { enabled: boolean; service: string };

/** Reject relative, credential-bearing and non-web links from calendar data. */
function conferenceLink(value: string | null | undefined): string | null {
  if (
    !value ||
    value.length > 2048 ||
    /[\s\\"'<>]/.test(value) ||
    [...value].some((char) => {
      const code = char.codePointAt(0) ?? 0;
      return code < 32 || (code >= 127 && code <= 159);
    }) ||
    !/^https?:\/\//i.test(value)
  )
    return null;
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") &&
      url.hostname &&
      !url.username &&
      !url.password &&
      url.port !== "0"
      ? url.href
      : null;
  } catch {
    return null;
  }
}

export function CalendarPanel({ onClose }: { onClose: () => void }) {
  const [offset, setOffset] = useState(0);
  const week = useQuery({
    queryKey: ["org", "calendar", offset],
    queryFn: async (): Promise<Week> => {
      const url = new URL("/api/org/calendar", window.location.origin);
      url.searchParams.set("offset", String(offset));
      const response = await fetch(new Request(url, { headers: { Authorization: `Bearer ${token() ?? ""}` } }));
      if (!response.ok) throw new Error("Calendar unavailable");
      return (await response.json()) as Week;
    },
    refetchInterval: 60_000,
    retry: false,
  });
  const reminders = useQuery({
    queryKey: ["org", "calendar-reminders-status"],
    queryFn: async (): Promise<ReminderStatus> => {
      const url = new URL("/api/org/calendar-reminders-status", window.location.origin);
      const response = await fetch(new Request(url, { headers: { Authorization: `Bearer ${token() ?? ""}` } }));
      if (!response.ok) throw new Error("Reminder status unavailable");
      return (await response.json()) as ReminderStatus;
    },
    refetchInterval: 60_000,
    retry: false,
  });
  return (
    <section aria-label="Calendar" className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-2 border-b border-border p-3">
        <h2 className="font-semibold">Calendar</h2>
        <button type="button" className="min-h-10 px-2" onClick={onClose} aria-label="Close calendar">
          Close
        </button>
      </header>
      <div className="border-b border-border p-3 text-sm" aria-live="polite">
        {reminders.isPending ? (
          "Checking reminders…"
        ) : reminders.isError ? (
          "Reminder status unavailable."
        ) : !reminders.data.enabled ? (
          <>
            Calendar reminders are off or unavailable.{" "}
            <Link to="?settings=notifications" className="underline">
              Notification settings
            </Link>
          </>
        ) : reminders.data.service === "running" ? (
          "Calendar reminder worker is running."
        ) : reminders.data.service === "not installed" ? (
          <>
            Calendar reminders need a worker. Run <code>hz org calendar-reminders-install</code> on this computer.
          </>
        ) : (
          "Calendar reminders are enabled, but the worker's running state is unavailable."
        )}
      </div>
      <div className="flex items-center justify-between gap-2 border-b border-border p-2">
        <button
          type="button"
          className="min-h-10 px-2"
          aria-label="Previous week"
          disabled={offset <= -52}
          onClick={() => setOffset(offset - 1)}
        >
          ←
        </button>
        <button type="button" className="min-h-10 px-2" onClick={() => setOffset(0)}>
          Today
        </button>
        <button
          type="button"
          className="min-h-10 px-2"
          aria-label="Next week"
          disabled={offset >= 52}
          onClick={() => setOffset(offset + 1)}
        >
          →
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3" aria-live="polite">
        {week.isPending ? (
          <p>Loading calendar…</p>
        ) : week.isError ? (
          <p>Calendar unavailable. Try again later.</p>
        ) : week.data.problem ? (
          <div>
            <p>{week.data.problem}</p>
            {week.data.fix && (
              <Link className="underline" to={`?settings=${CONNECTORS}`}>
                Connect a calendar…
              </Link>
            )}
          </div>
        ) : (
          <>
            <p className="mb-3 font-medium">
              {new Date(week.data.since).toLocaleDateString(undefined, {
                dateStyle: "medium",
                timeZone: week.data.timezone ?? undefined,
              })}{" "}
              –{" "}
              {new Date(new Date(week.data.until).getTime() - 1).toLocaleDateString(undefined, {
                dateStyle: "medium",
                timeZone: week.data.timezone ?? undefined,
              })}
            </p>
            {week.data.events.length === 0 ? (
              <p>No events this week.</p>
            ) : (
              <ol className="space-y-3">
                {week.data.events.map((event) => (
                  <li key={`${event.id}:${event.start}`} className="rounded-lg border border-border p-3">
                    <p className="font-medium break-words">{event.title || "(no title)"}</p>
                    <p className="text-muted-foreground">
                      {event.all_day ? "All day · " : ""}
                      {new Date(event.start).toLocaleString(undefined, {
                        dateStyle: "medium",
                        timeZone: week.data.timezone ?? undefined,
                        ...(event.all_day ? {} : { timeStyle: "short", hourCycle: "h23" }),
                      })}
                      {!event.all_day &&
                        ` – ${new Date(event.end).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short", hourCycle: "h23", timeZone: week.data.timezone ?? undefined })}`}
                    </p>
                    {conferenceLink(event.conference_url) && (
                      <a
                        href={conferenceLink(event.conference_url) ?? undefined}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="inline-flex min-h-10 items-center text-primary underline"
                      >
                        Join meeting
                      </a>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </div>
    </section>
  );
}
