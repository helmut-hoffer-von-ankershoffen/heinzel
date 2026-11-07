import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, Video, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { token } from "@/api/token";
import { OperationDialog } from "@/components/OperationDialog";
import { isLocalServer } from "@/lib/browser";
import { conferenceLink, conferenceProvider } from "@/lib/conferenceLink";
import { requestOperation } from "@/lib/operationRun";
import { CONNECTORS } from "./settingsSections";

type Person = { name?: string | null; email?: string | null };
type Event = {
  id: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  conference_url?: string | null;
  url?: string | null;
  location?: string | null;
  organizer?: Person | null;
  attendees?: Person[];
  response?: "accepted" | "declined" | "tentative" | "pending" | null;
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

export function CalendarPanel({ onClose }: { onClose: () => void }) {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const week = useQuery({
    queryKey: ["org", "calendar", offset],
    // The query aborts its older read when it is cancelled or superseded, so a stale response never overwrites newer data.
    queryFn: async ({ signal }): Promise<Week> => {
      const url = new URL("/api/org/calendar", window.location.origin);
      url.searchParams.set("offset", String(offset));
      const response = await fetch(
        new Request(url, {
          signal,
          cache: "no-store",
          headers: { Authorization: `Bearer ${token() ?? ""}` },
        }),
      );
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
      const response = await fetch(
        new Request(url, {
          headers: { Authorization: `Bearer ${token() ?? ""}` },
        }),
      );
      if (!response.ok) throw new Error("Reminder status unavailable");
      return (await response.json()) as ReminderStatus;
    },
    refetchInterval: 60_000,
    retry: false,
  });
  const chosen = week.data?.events.find((event) => `${event.id}:${event.start}` === selected);
  return (
    <section aria-label="Calendar" className="flex h-full min-h-0 flex-col">
      <OperationDialog />
      <header className="flex items-center justify-between gap-2 border-b border-border p-3">
        <h2 className="font-semibold">Calendar</h2>
        <button
          type="button"
          className="flex min-h-10 min-w-10 items-center justify-center rounded-lg hover:bg-accent"
          onClick={onClose}
          aria-label="Close calendar"
          title="Close calendar"
        >
          <X aria-hidden className="size-4" />
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
          <>Calendar reminders need a background worker on this computer.</>
        ) : (
          "Calendar reminders are enabled, but the worker's running state is unavailable."
        )}
        {reminders.data && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {isLocalServer() ? (
              <>
                <button
                  type="button"
                  className="min-h-10 px-2 underline"
                  onClick={() =>
                    requestOperation({
                      method: "POST",
                      path: `/org/calendar-reminders-${reminders.data.service === "running" ? "uninstall" : "install"}`,
                      query: { confirm: true },
                      body: null,
                      read_only: false,
                      command: "",
                      summary:
                        reminders.data.service === "running"
                          ? "Remove this profile's calendar reminder worker from login?"
                          : "Install a calendar reminder worker for this profile at every login? Calendar reads require separate consent in Notification settings.",
                    })
                  }
                >
                  {reminders.data.service === "running" ? "Remove reminder worker" : "Set up reminder worker"}
                </button>
                {reminders.data.service !== "running" && reminders.data.service !== "not installed" && (
                  <button
                    type="button"
                    className="min-h-10 px-2 underline"
                    onClick={() =>
                      requestOperation({
                        method: "POST",
                        path: "/org/calendar-reminders-uninstall",
                        query: { confirm: true },
                        body: null,
                        read_only: false,
                        command: "",
                        summary: "Remove this profile's calendar reminder worker from login?",
                      })
                    }
                  >
                    Remove reminder worker
                  </button>
                )}
              </>
            ) : (
              <p>Open Calendar on this computer to set up its reminder worker.</p>
            )}
          </div>
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
            {chosen ? (
              <EventDetail event={chosen} timezone={week.data.timezone} onBack={() => setSelected(null)} />
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
                    {week.data.events.map((event) => {
                      const key = `${event.id}:${event.start}`;
                      const join = conferenceLink(event.conference_url);
                      return (
                        <li key={key} className="flex items-start gap-1 rounded-lg border border-border p-1">
                          <button
                            type="button"
                            className="min-h-11 min-w-0 flex-1 rounded-md p-2 text-left hover:bg-accent"
                            onClick={() => setSelected(key)}
                          >
                            <span className="block font-medium break-words">{event.title || "(no title)"}</span>
                            <span className="block text-muted-foreground">{when(event, week.data.timezone)}</span>
                          </button>
                          {join && <IconLink href={join} label="Join meeting" icon={Video} />}
                        </li>
                      );
                    })}
                  </ol>
                )}
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function when(event: Event, timezone: string | null): string {
  const options: Intl.DateTimeFormatOptions = {
    dateStyle: "medium",
    timeZone: timezone ?? undefined,
  };
  if (!event.all_day) Object.assign(options, { timeStyle: "short", hourCycle: "h23" });
  const format = (date: string) => new Date(date).toLocaleString(undefined, options);
  return `${event.all_day ? "All day · " : ""}${format(event.start)}${event.all_day ? "" : ` – ${format(event.end)}`}`;
}

const responses = {
  accepted: "Accepted",
  declined: "Declined",
  tentative: "Tentative",
  pending: "Awaiting reply",
};
const personName = (person: Person) => person.name || person.email || "Name unavailable";

function IconLink({ href, label, icon: Icon }: { href: string; label: string; icon: typeof Video }) {
  return (
    <a
      href={href}
      aria-label={label}
      title={label}
      target="_blank"
      rel="noreferrer noopener"
      className="flex size-11 shrink-0 items-center justify-center rounded-md text-primary hover:bg-accent"
    >
      <Icon aria-hidden className="size-4" />
    </a>
  );
}

/** One meeting in full: provider and a Join icon button, then who organizes and who is invited. Read-only (phase 1). */
function EventDetail({ event, timezone, onBack }: { event: Event; timezone: string | null; onBack: () => void }) {
  const join = conferenceLink(event.conference_url);
  const provider = conferenceProvider(event.conference_url);
  const source = conferenceLink(event.url);
  const attendees = [...new Set((event.attendees ?? []).map(personName))];
  return (
    <article aria-label={event.title || "(no title)"} className="space-y-3">
      <div className="flex items-start gap-1">
        <button
          type="button"
          aria-label="Back to the week"
          title="Back to the week"
          className="flex size-11 shrink-0 items-center justify-center rounded-md hover:bg-accent"
          onClick={onBack}
        >
          <ArrowLeft aria-hidden className="size-4" />
        </button>
        <div className="min-w-0 flex-1 py-2">
          <h3 className="font-medium break-words">{event.title || "(no title)"}</h3>
          <p className="text-muted-foreground">{when(event, timezone)}</p>
        </div>
        {join && <IconLink href={join} label="Join meeting" icon={Video} />}
        {source && <IconLink href={source} label="Open in calendar" icon={ExternalLink} />}
      </div>
      <dl className="space-y-2">
        {join && <Fact term="Provider" value={provider ?? new URL(join).hostname} />}
        {event.location && <Fact term="Location" value={event.location} />}
        {event.organizer && <Fact term="Organizer" value={personName(event.organizer)} />}
        {event.response && <Fact term="Your reply" value={responses[event.response]} />}
        {attendees.length > 0 && (
          <div>
            <dt className="text-muted-foreground">Attendees</dt>
            <dd>
              <ul className="space-y-1">
                {attendees.map((name) => (
                  <li key={name} className="break-words">
                    {name}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        )}
      </dl>
    </article>
  );
}

function Fact({ term, value }: { term: string; value: string }) {
  return (
    <div>
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="break-words">{value}</dd>
    </div>
  );
}
