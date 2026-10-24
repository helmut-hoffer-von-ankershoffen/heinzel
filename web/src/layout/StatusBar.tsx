// The status bar under the screen: Heinzelmännchen and its version on the left; the meeting running now or the next
// one, and a clock, on the right. The meeting comes from `hz org agenda` (the bound calendar), refreshed each minute.

import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Video } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router";
import { useInfo } from "@/api/queries";
import { token } from "@/api/token";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

type Meeting = { title: string; start: string; end: string; conference_url?: string | null };
type Agenda = { current: Meeting | null; next: Meeting | null; problem: string | null; fix: string | null };

/** `GET /api/org/agenda`; answers 404 until the server has it, which shows as no meeting. */
function useAgenda() {
  return useQuery({
    queryKey: ["org", "agenda"],
    queryFn: async (): Promise<Agenda | null> => {
      const request = new Request(new URL("/api/org/agenda", window.location.origin), {
        headers: { Authorization: `Bearer ${token() ?? ""}` },
      });
      const response = await fetch(request);
      return response.ok ? ((await response.json()) as Agenda) : null;
    },
    refetchInterval: 60_000,
    retry: false,
  });
}

type ZoneTime = { zone: string; time: string; hours: [number, number]; working: boolean };

/** `GET /api/clock` from the clock plugin: the team's zones and whether they're at work; null without the plugin. */
function useZones() {
  // Only with the clock plugin: without it there is no /api/clock to ask, and asking would log a 404.
  const info = useInfo();
  const enabled = Boolean(info.data?.modules.some((module) => module.qualname === "clock"));
  return useQuery({
    enabled,
    queryKey: ["clock"],
    queryFn: async (): Promise<ZoneTime[] | null> => {
      const request = new Request(new URL("/api/clock", window.location.origin), {
        headers: { Authorization: `Bearer ${token() ?? ""}` },
      });
      const response = await fetch(request);
      return response.ok ? ((await response.json()) as ZoneTime[]) : null;
    },
    refetchInterval: 60_000,
    retry: false,
  });
}

type Parts = { weekday: number; hour: number; minute: number; day: string; offset: number };
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** The weekday (0 = Sunday), hour and minute in `zone` now, and its offset from this machine in minutes. */
function partsIn(zone: string, now: Date): Parts {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      weekday: "short",
      hour: "numeric",
      minute: "numeric",
      hourCycle: "h23",
    })
      .formatToParts(now)
      .map((part) => [part.type, part.value]),
  );
  const local = new Date(now.toLocaleString("en-US", { timeZone: zone }));
  const here = new Date(now.toLocaleString("en-US"));
  return {
    weekday: WEEKDAYS.indexOf(parts.weekday ?? "Mon"),
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    day: parts.weekday ?? "",
    offset: Math.round((local.getTime() - here.getTime()) / 60_000),
  };
}

/** Whether they're reachable now, and if not, when they're back: what the hover answers. */
function availability(zone: ZoneTime, at: Parts): string {
  const [start, end] = zone.hours;
  const from = `${pad(start)}:00`;
  const weekend = at.weekday === 0 || at.weekday === 6;
  const minutes = at.hour * 60 + at.minute;
  if (weekend) return t("status.weekend", { from });
  if (minutes < start * 60) {
    const left = start * 60 - minutes;
    return left <= 120
      ? t("status.startsIn", {
          from,
          minutes: left < 60 ? `${left} min` : `${Math.floor(left / 60)} h ${left % 60} min`,
        })
      : t("status.starts", { from });
  }
  if (minutes < end * 60) return t("status.workingUntil", { until: `${pad(end)}:00` });
  return at.weekday === 5 ? t("status.backMonday", { from }) : t("status.backTomorrow", { from });
}

const offsetText = (minutes: number) => {
  if (minutes === 0) return t("status.yourTime");
  const hours = Math.abs(minutes) / 60;
  return `${minutes > 0 ? "+" : "−"}${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`;
};

/** `America/Los_Angeles` → `Los Angeles`. */
const city = (zone: string) => (zone.split("/").at(-1) ?? zone).replaceAll("_", " ");
const pad = (hour: number) => String(hour).padStart(2, "0");

/** The clock, with the team's zones on hover or focus: each one's time and whether it's within working hours. */
function Clock({ now }: { now: Date }) {
  const zones = useZones();
  const [open, setOpen] = useState(false);
  const zonesId = useId();
  const list = zones.data;
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: hover only previews what the focusable time button shows
    <span className="relative shrink-0" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-expanded={open}
        aria-describedby={zonesId}
        className="rounded px-1 font-mono text-foreground/80 tabular-nums hover:bg-accent/60"
      >
        <time dateTime={now.toISOString()}>
          {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
        </time>
        <span id={zonesId} className="sr-only">
          {t("status.zones")}
        </span>
      </button>
      {open && (
        <div
          role="tooltip"
          className="absolute right-0 bottom-full z-40 mb-2 w-80 rounded-xl border border-border bg-popover p-1.5 text-sm shadow-xl"
        >
          {list && list.length > 0 ? (
            <ul>
              {list.map((zone) => {
                const at = partsIn(zone.zone, now);
                const sameDay = at.weekday === now.getDay();
                return (
                  <li key={zone.zone} className="flex items-center gap-3 rounded-lg px-2.5 py-2">
                    <span
                      aria-hidden
                      className={cn("size-2 shrink-0 rounded-full", zone.working ? "bg-ok" : "bg-muted-foreground/40")}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline gap-2">
                        <span className="truncate text-foreground">{city(zone.zone)}</span>
                        <span className="text-xs text-muted-foreground">{offsetText(at.offset)}</span>
                      </span>
                      <span className={cn("block text-xs", zone.working ? "text-ok" : "text-muted-foreground")}>
                        {availability(zone, at)}
                      </span>
                    </span>
                    <span className="shrink-0 font-mono text-foreground tabular-nums">
                      {sameDay ? "" : `${at.day} `}
                      {pad(at.hour)}:{pad(at.minute)}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="px-2.5 py-1.5 text-muted-foreground">{t("status.noZones")}</p>
          )}
          <a
            href="?settings=clock"
            className="mt-1 block rounded-lg border-t border-border px-2.5 pt-2 pb-1 text-xs text-muted-foreground hover:text-foreground"
          >
            {t("status.editZones")}
          </a>
        </div>
      )}
    </span>
  );
}

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tick = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(tick);
  }, []);
  return now;
}

const time = (date: Date) => date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** "in 12 min", "in 2 h 5 min", or the time when it's tomorrow or later. */
function until(start: Date, now: Date): string {
  const minutes = Math.round((start.getTime() - now.getTime()) / 60_000);
  if (minutes < 1) return t("status.now");
  if (start.toDateString() !== now.toDateString()) return time(start);
  if (minutes < 60) return t("status.inMinutes", { minutes });
  return t("status.inHours", { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
}

export function StatusBar() {
  const info = useInfo();
  const agenda = useAgenda();
  const now = useNow();
  const current = agenda.data?.current;
  const next = agenda.data?.next;
  return (
    <footer className="flex h-[60px] min-w-0 shrink-0 items-center gap-3 border-t-[0.5px] border-foreground/20 px-4 text-ui text-muted-foreground">
      <span className="flex min-w-0 items-center gap-2">
        <span aria-hidden className="shrink-0 text-xl leading-none">
          🧝
        </span>
        <span className="truncate text-foreground">Heinzelmännchen</span>
        {info.data && (
          <Link
            to="?panel=changelog"
            title={t("account.changelog")}
            className="shrink-0 rounded px-1 text-ui-sm hover:bg-accent/60 hover:text-foreground max-sm:hidden"
          >
            v{info.data.version}
          </Link>
        )}
      </span>
      <span className="ml-auto flex min-w-0 items-center gap-1.5">
        {current ? (
          <>
            <Video aria-hidden className="size-3.5 shrink-0 text-primary" />
            <span className="truncate">
              {t("status.current", { title: current.title, end: time(new Date(current.end)) })}
            </span>
            {current.conference_url && (
              <a
                href={current.conference_url}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 text-primary underline"
              >
                {t("status.join")}
              </a>
            )}
          </>
        ) : next ? (
          <>
            <CalendarClock aria-hidden className="size-3.5 shrink-0" />
            <span className="truncate">
              {t("status.next", { title: next.title, when: until(new Date(next.start), now) })}
            </span>
          </>
        ) : agenda.data?.problem ? (
          <span className="truncate" title={agenda.data.fix ?? undefined}>
            {t("status.noCalendar")}
          </span>
        ) : null}
      </span>
      <Clock now={now} />
    </footer>
  );
}
