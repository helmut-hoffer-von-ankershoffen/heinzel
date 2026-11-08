import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Bell,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  type LucideIcon,
  Plug,
  Trash2,
  Video,
  Wrench,
  X,
} from "lucide-react";
import { useState } from "react";
import { token } from "@/api/token";
import { OperationDialog } from "@/components/OperationDialog";
import { PaneAction, PaneActions, PaneTitle } from "@/components/PaneActions";
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
      <header className="flex items-center border-b border-border p-3">
        <PaneTitle
          as="h2"
          chrome={
            <PaneAction ghost icon={X} onClick={onClose}>
              Close calendar
            </PaneAction>
          }
        >
          Calendar
        </PaneTitle>
      </header>
      <div className="border-b border-border p-3 text-sm" aria-live="polite">
        {reminders.isPending ? (
          "Checking reminders…"
        ) : reminders.isError ? (
          "Reminder status unavailable."
        ) : !reminders.data.enabled ? (
          "Calendar reminders are off or unavailable."
        ) : reminders.data.service === "running" ? (
          "Calendar reminder worker is running."
        ) : reminders.data.service === "not installed" ? (
          <>Calendar reminders need a background worker on this computer.</>
        ) : (
          "Calendar reminders are enabled, but the worker's running state is unavailable."
        )}
        {reminders.data && !isLocalServer() && (
          <p className="mt-2">Open Calendar on this computer to set up its reminder worker.</p>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3" aria-live="polite">
        {week.isPending ? (
          <p>Loading calendar…</p>
        ) : week.isError ? (
          <p>Calendar unavailable. Try again later.</p>
        ) : week.data.problem ? (
          <div>
            <p>{week.data.problem}</p>
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
      <PaneActions className="mx-3 mb-2">
        <PaneAction key="today" icon={CalendarDays} onClick={() => setOffset(0)}>
          Today
        </PaneAction>
        <PaneAction key="previous" icon={ChevronLeft} disabled={offset <= -52} onClick={() => setOffset(offset - 1)}>
          Previous week
        </PaneAction>
        <PaneAction key="next" icon={ChevronRight} disabled={offset >= 52} onClick={() => setOffset(offset + 1)}>
          Next week
        </PaneAction>
        {reminders.data && !reminders.data.enabled && (
          <PaneAction key="notifications" icon={Bell} to="?settings=notifications">
            Notification settings
          </PaneAction>
        )}
        {week.data?.problem && week.data.fix && (
          <PaneAction key="connect" icon={Plug} to={`?settings=${CONNECTORS}`}>
            Connect a calendar…
          </PaneAction>
        )}
        {reminders.data && isLocalServer() && (
          <PaneAction
            key="worker"
            icon={reminders.data.service === "running" ? Trash2 : Wrench}
            destructive={reminders.data.service === "running"}
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
          </PaneAction>
        )}
        {reminders.data &&
          isLocalServer() &&
          reminders.data.service !== "running" &&
          reminders.data.service !== "not installed" && (
            <PaneAction
              key="remove"
              icon={Trash2}
              destructive
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
            </PaneAction>
          )}
      </PaneActions>
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

function IconLink({ href, label, icon }: { href: string; label: string; icon: LucideIcon }) {
  return (
    <PaneAction ghost icon={icon} href={href}>
      {label}
    </PaneAction>
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
        <PaneAction ghost icon={ArrowLeft} onClick={onBack}>
          Back to the week
        </PaneAction>
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
