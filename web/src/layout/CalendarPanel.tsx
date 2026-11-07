import { useQuery } from "@tanstack/react-query";
import { Bell, CalendarDays, ChevronLeft, ChevronRight, Plug, Trash2, Video, Wrench, X } from "lucide-react";
import { useState } from "react";
import { token } from "@/api/token";
import { OperationDialog } from "@/components/OperationDialog";
import { PaneAction, PaneActions, PaneTitle } from "@/components/PaneActions";
import { isLocalServer } from "@/lib/browser";
import { requestOperation } from "@/lib/operationRun";
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
                      <PaneAction ghost icon={Video} href={conferenceLink(event.conference_url) ?? undefined}>
                        Join meeting
                      </PaneAction>
                    )}
                  </li>
                ))}
              </ol>
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
