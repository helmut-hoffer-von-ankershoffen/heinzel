import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { token } from "@/api/token";
import { CONNECTORS } from "./settingsSections";

type Event = { id: string; title: string; start: string; end: string; all_day: boolean };
type Week = {
  timezone: string | null;
  since: string;
  until: string;
  events: Event[];
  problem: string | null;
  fix: string | null;
};

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
  return (
    <section aria-label="Calendar" className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-2 border-b border-border p-3">
        <h2 className="font-semibold">Calendar</h2>
        <button type="button" className="min-h-10 px-2" onClick={onClose} aria-label="Close calendar">
          Close
        </button>
      </header>
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
