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
  TriangleAlert,
  Video,
  Wrench,
  X,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { token } from "@/api/token";
import { OperationDialog } from "@/components/OperationDialog";
import { PaneAction, PaneActions, PaneTitle } from "@/components/PaneActions";
import { t } from "@/i18n";
import { isLocalServer } from "@/lib/browser";
import { conferenceLink, conferenceProvider } from "@/lib/conferenceLink";
import { requestOperation } from "@/lib/operationRun";
import { cn } from "@/lib/utils";
import {
  clock,
  type Day,
  type DayEvent,
  groupWeek,
  nextEvent,
  nowIndex,
  rowState,
  rowTimes,
  weekCaption,
} from "./agendaModel";
import { spanText } from "./calendarModel";
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

/** The client clock at `interval` resolution; the agenda re-derives Now from it. */
function useNow(interval: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), interval);
    return () => window.clearInterval(id);
  }, [interval]);
  return now;
}

/** Brings Today to the top of the list; no smooth scrolling for people who asked for less motion. */
function showToday(node: HTMLElement | null) {
  const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  node?.scrollIntoView?.({ block: "start", behavior: calm ? "auto" : "smooth" });
}

export function CalendarPanel({ onClose }: { onClose: () => void }) {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const now = useNow(60_000);
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
      if (!response.ok) throw new Error(t("calendar.unavailable"));
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
      if (!response.ok) throw new Error(t("calendar.reminderStatusFailed"));
      return (await response.json()) as ReminderStatus;
    },
    refetchInterval: 60_000,
    retry: false,
  });
  const chosen = week.data?.events.find((event) => `${event.id}:${event.start}` === selected);
  const data = week.data && !week.data.problem ? week.data : undefined;
  const days = data
    ? groupWeek({ events: data.events, since: data.since, until: data.until, now, tz: data.timezone })
    : [];
  const today = days.find((day) => day.relation === "today");
  const workerAction = (installing: boolean) =>
    requestOperation({
      method: "POST",
      path: `/org/calendar-reminders-${installing ? "install" : "uninstall"}`,
      query: { confirm: true },
      body: null,
      read_only: false,
      command: "",
      summary: t(installing ? "calendar.installSummary" : "calendar.removeSummary"),
    });
  return (
    <section aria-label={t("calendar.title")} className="flex h-full min-h-0 flex-col">
      <OperationDialog />
      <header className="flex items-center border-b border-border p-3">
        <PaneTitle
          as="h2"
          chrome={
            <PaneAction ghost icon={X} onClick={onClose}>
              {t("calendar.close")}
            </PaneAction>
          }
        >
          {t("calendar.title")}
        </PaneTitle>
      </header>
      <ReminderBand reminders={reminders} />
      {today && data && !chosen && <TodayStatus day={today} now={now} tz={data.timezone} />}
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {week.isPending ? (
          <p className="pt-3">{t("calendar.loading")}</p>
        ) : week.isError ? (
          <p className="pt-3">{t("calendar.unavailable")}</p>
        ) : week.data.problem ? (
          <div className="pt-3">
            <p>{week.data.problem}</p>
          </div>
        ) : chosen ? (
          <div className="pt-3">
            <EventDetail event={chosen} timezone={week.data.timezone} onBack={() => setSelected(null)} />
          </div>
        ) : (
          <>
            {!today && (
              <p className="pt-3 text-sm text-muted-foreground">
                {weekCaption(week.data.since, week.data.until, week.data.timezone)}
              </p>
            )}
            {days.length === 0 ? (
              <p className="pt-3">{t("calendar.noEventsWeek")}</p>
            ) : (
              <ol aria-label={t("calendar.agenda")}>
                {days.map((day) => (
                  <DayGroup
                    key={day.key}
                    day={day}
                    now={now}
                    tz={week.data.timezone}
                    onOpen={(event) => setSelected(`${event.id}:${event.start}`)}
                  />
                ))}
              </ol>
            )}
          </>
        )}
      </div>
      <PaneActions className="mx-3 mb-2">
        <PaneAction key="today" icon={CalendarDays} disabled={offset === 0} onClick={() => setOffset(0)}>
          {t("calendar.today")}
        </PaneAction>
        <PaneAction key="previous" icon={ChevronLeft} disabled={offset <= -52} onClick={() => setOffset(offset - 1)}>
          {t("calendar.previousWeek")}
        </PaneAction>
        <PaneAction key="next" icon={ChevronRight} disabled={offset >= 52} onClick={() => setOffset(offset + 1)}>
          {t("calendar.nextWeek")}
        </PaneAction>
        {reminders.data && !reminders.data.enabled && (
          <PaneAction key="notifications" icon={Bell} to="?settings=notifications">
            {t("calendar.notificationSettings")}
          </PaneAction>
        )}
        {week.data?.problem && week.data.fix && (
          <PaneAction key="connect" icon={Plug} to={`?settings=${CONNECTORS}`}>
            {t("calendar.connect")}
          </PaneAction>
        )}
        {reminders.data && isLocalServer() && (
          <PaneAction
            key="worker"
            icon={reminders.data.service === "running" ? Trash2 : Wrench}
            destructive={reminders.data.service === "running"}
            onClick={() => workerAction(reminders.data.service !== "running")}
          >
            {t(reminders.data.service === "running" ? "calendar.removeWorker" : "calendar.setUpWorker")}
          </PaneAction>
        )}
        {reminders.data &&
          isLocalServer() &&
          reminders.data.service !== "running" &&
          reminders.data.service !== "not installed" && (
            <PaneAction key="remove" icon={Trash2} destructive onClick={() => workerAction(false)}>
              {t("calendar.removeWorker")}
            </PaneAction>
          )}
      </PaneActions>
    </section>
  );
}

/** One line, icon plus sentence, nothing to click: the fix is the worker button in the bottom row. */
function ReminderBand({ reminders }: { reminders: { data?: ReminderStatus; isPending: boolean; isError: boolean } }) {
  let line: { icon: LucideIcon; text: string } | null = null;
  if (reminders.isError) line = { icon: TriangleAlert, text: t("calendar.statusUnavailable") };
  else if (reminders.data) {
    const { enabled, service } = reminders.data;
    if (!enabled) line = { icon: Bell, text: t("calendar.remindersOff") };
    else if (service === "not installed") {
      line = isLocalServer()
        ? { icon: Wrench, text: t("calendar.workerNeeded") }
        : { icon: Wrench, text: t("calendar.workerRemote") };
    } else if (service !== "running") line = { icon: TriangleAlert, text: t("calendar.workerUnknown") };
  }
  if (!line) return null;
  const Icon = line.icon;
  return (
    <p className="flex items-center gap-2 border-b border-border px-3 py-2 text-sm text-muted-foreground">
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      {line.text}
    </p>
  );
}

/** The one polite live line, updated per minute: what is on now and what comes next. */
function TodayStatus({ day, now, tz }: { day: Day<Event>; now: number; tz: string | null }) {
  const running = day.events.find((item) => rowState(item.event, now).state === "running");
  const next = nextEvent(day.events, now);
  const nothing = t("calendar.statusNothing");
  const now_ = running
    ? t("calendar.statusLeft", {
        title: running.event.title || t("calendar.noTitle"),
        left: t("calendar.left", { span: spanText(rowState(running.event, now).minutesLeft) }),
      })
    : nothing;
  const after = next
    ? t("calendar.statusNext", {
        title: next.event.title || t("calendar.noTitle"),
        startsIn: t("calendar.startsIn", { span: spanText(rowState(next.event, now).startsIn) }),
      })
    : nothing;
  return (
    <p role="status" className="sr-only" data-time={clock(now, tz)}>
      {t("calendar.status", { now: now_, next: after })}
    </p>
  );
}

function DayGroup({
  day,
  now,
  tz,
  onOpen,
}: {
  day: Day<Event>;
  now: number;
  tz: string | null;
  onOpen: (event: Event) => void;
}) {
  const isToday = day.relation === "today";
  const id = `agenda-day-${day.key}`;
  const relation = day.relation && t(`calendar.${day.relation}`);
  const split = isToday ? nowIndex(day.events, now) : -1;
  const next = isToday ? nextEvent(day.events, now) : undefined;
  const rows = (items: DayEvent<Event>[]): ReactNode =>
    items.length > 0 && (
      <ul className="space-y-1">
        {items.map((item) => (
          <EventRow
            key={`${item.event.id}:${item.event.start}:${item.edge}`}
            item={item}
            tz={tz}
            now={now}
            dim={day.tense === "today" && rowState(item.event, now).state === "past"}
            next={next === item}
            onOpen={onOpen}
          />
        ))}
      </ul>
    );
  return (
    <li>
      <section
        aria-labelledby={id}
        aria-current={isToday ? "date" : undefined}
        ref={isToday ? showToday : undefined}
        className={cn(day.tense === "past" && "opacity-60")}
      >
        <h3
          id={id}
          className={cn(
            "sticky top-0 z-10 flex items-center gap-1.5 bg-background py-2 text-[13px] font-medium tracking-wide uppercase",
            isToday ? "text-foreground" : "text-muted-foreground",
          )}
        >
          {isToday && <span className="size-2 rounded-full bg-primary" aria-hidden="true" />}
          {relation ? t("calendar.dayHeader", { relation, date: day.date }) : day.date}
        </h3>
        {day.events.length === 0 ? (
          <p className="pb-2 text-sm text-muted-foreground">{t("calendar.noEventsToday")}</p>
        ) : (
          <>
            {rows(day.events.slice(0, split === -1 ? undefined : split))}
            {isToday && <NowLine now={now} tz={tz} />}
            {split !== -1 && rows(day.events.slice(split))}
          </>
        )}
      </section>
    </li>
  );
}

function NowLine({ now, tz }: { now: number; tz: string | null }) {
  const time = clock(now, tz);
  return (
    <div className="my-1 flex items-center gap-2 text-xs text-primary tabular-nums">
      <hr aria-label={t("calendar.nowAt", { time })} className="m-0 h-px flex-1 border-0 bg-primary" />
      <span aria-hidden="true">{time}</span>
      <span className="h-px flex-1 bg-primary" aria-hidden="true" />
    </div>
  );
}

function EventRow({
  item,
  tz,
  now,
  dim,
  next,
  onOpen,
}: {
  item: DayEvent<Event>;
  tz: string | null;
  now: number;
  dim: boolean;
  next: boolean;
  onOpen: (event: Event) => void;
}) {
  const { event, edge } = item;
  const join = conferenceLink(event.conference_url);
  const times = rowTimes(item, tz);
  const state = rowState(event, now);
  const running = state.state === "running";
  const title = event.title || t("calendar.noTitle");
  const when = times.allDay
    ? t("calendar.allDay")
    : edge === "start"
      ? t("calendar.fromTime", { time: clock(event.start, tz) })
      : edge === "end"
        ? t("calendar.untilTime", { time: clock(event.end, tz) })
        : t("calendar.timeRange", { start: times.top ?? "", end: times.bottom ?? "" });
  const left = t("calendar.left", { span: spanText(state.minutesLeft) });
  const startsIn = t("calendar.startsIn", { span: spanText(state.startsIn) });
  const name = running
    ? t("calendar.rowNow", { title, when, span: spanText(state.minutesLeft) })
    : next
      ? t("calendar.rowNext", { title, when, startsIn })
      : t("calendar.rowName", { title, when });
  return (
    <li
      className={cn(
        "flex items-start gap-1 border-l-2 border-transparent",
        running && "border-primary",
        dim && "opacity-60",
      )}
    >
      <button
        type="button"
        aria-label={name}
        className="flex min-h-11 min-w-0 flex-1 gap-3 rounded-md p-2 text-left hover:bg-accent"
        onClick={() => onOpen(event)}
      >
        <span className="w-12 shrink-0 text-sm text-muted-foreground tabular-nums">
          {times.allDay ? (
            t("calendar.allDay")
          ) : (
            <>
              <span className="block">{times.top}</span>
              {times.bottom && <span className="block">{times.bottom}</span>}
            </>
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-medium break-words">{title}</span>
          {running && (
            <>
              <span className="mt-0.5 flex items-center gap-2 text-sm">
                <span className="rounded-sm bg-primary px-1.5 text-[11px] font-medium tracking-wide text-primary-foreground uppercase">
                  {t("calendar.now")}
                </span>
                <span className="text-muted-foreground">{left}</span>
              </span>
              <span className="mt-1 block h-0.5 bg-border" aria-hidden="true">
                <span
                  data-progress=""
                  className="block h-0.5 bg-primary"
                  style={{ width: `${Math.round(state.fraction * 100)}%` }}
                />
              </span>
            </>
          )}
          {next && <span className="block text-sm text-muted-foreground">{startsIn}</span>}
        </span>
      </button>
      {join && <IconLink href={join} label={joinLabel(event.conference_url)} icon={Video} />}
    </li>
  );
}

function when(event: Event, timezone: string | null): string {
  const options: Intl.DateTimeFormatOptions = {
    dateStyle: "medium",
    timeZone: timezone ?? undefined,
  };
  if (!event.all_day) Object.assign(options, { timeStyle: "short", hourCycle: "h23" });
  const format = (date: string) => new Date(date).toLocaleString(undefined, options);
  return `${event.all_day ? `${t("calendar.allDay")} \u00b7 ` : ""}${format(event.start)}${event.all_day ? "" : ` – ${format(event.end)}`}`;
}

const personName = (person: Person) => person.name || person.email || t("calendar.nameUnavailable");

/** "Join Zoom meeting" for a known tool, else "Join meeting". */
function joinLabel(url: string | null | undefined): string {
  const provider = conferenceProvider(url);
  return provider ? t("calendar.joinProvider", { provider }) : t("calendar.join");
}

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
    <article aria-label={event.title || t("calendar.noTitle")} className="space-y-3">
      <div className="flex items-start gap-1">
        <PaneAction ghost icon={ArrowLeft} onClick={onBack}>
          {t("calendar.backToWeek")}
        </PaneAction>
        <div className="min-w-0 flex-1 py-2">
          <h3 className="font-medium break-words">{event.title || t("calendar.noTitle")}</h3>
          <p className="text-muted-foreground">{when(event, timezone)}</p>
        </div>
        {join && <IconLink href={join} label={joinLabel(event.conference_url)} icon={Video} />}
        {source && <IconLink href={source} label={t("calendar.openInCalendar")} icon={ExternalLink} />}
      </div>
      <dl className="space-y-2">
        {join && <Fact term={t("calendar.provider")} value={provider ?? new URL(join).hostname} />}
        {event.location && <Fact term={t("calendar.location")} value={event.location} />}
        {event.organizer && <Fact term={t("calendar.organizer")} value={personName(event.organizer)} />}
        {event.response && <Fact term={t("calendar.yourReply")} value={t(`calendar.reply.${event.response}`)} />}
        {attendees.length > 0 && (
          <div>
            <dt className="text-muted-foreground">{t("calendar.attendees")}</dt>
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
