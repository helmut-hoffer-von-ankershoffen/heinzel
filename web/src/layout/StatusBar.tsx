// The status bar under the screen: hz's version on the left; the meeting running now or the next
// one, and a clock, on the right, then what modules add (ADR 0056). The meeting comes from `hz org agenda` (the bound calendar), refreshed each minute.

import { useQuery } from "@tanstack/react-query";
import { Calendar, CalendarClock, Video } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router";
import { useChangelog, useInfo } from "@/api/queries";
import { token } from "@/api/token";
import { NATIVE_FOOTER, StatusItems, useShown } from "@/components/Contributions";
import { FooterItem } from "@/components/FooterItem";
import { FOOTER_RELOCATED, FooterLayout, FooterMore, FooterPart, useFooterVisibility } from "@/components/FooterLayout";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { Prose } from "@/components/Prose";
import { SettingsShortcut } from "@/components/SettingsShortcut";
import { WorkStateIcon } from "@/components/WorkStateIcon";
import { t } from "@/i18n";
import { workingHours } from "@/lib/workingHours";
import { zoneClock, zoneTime } from "@/lib/zoneTime";
import { placePopover } from "@/sound/dock";
import { DurableHeinzelStatus } from "./DurableHeinzelStatus";
import { HealthStatus } from "./HealthStatus";
import { ResourceStatus } from "./ResourceStatus";
import { CLOCK_SETTINGS, CONNECTORS } from "./settingsSections";
import { UpdateStatus } from "./UpdateStatus";

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

export type ZoneTime = { zone: string; time: string; hours: [number, number]; working: boolean };

/** `GET /api/clock` from the clock plugin: the team's zones and whether they're at work; null without the plugin. */
export function useZones() {
  // Only with the clock plugin: without it there is no /api/clock to ask, and asking would log a 404.
  const info = useInfo();
  const enabled = Boolean(info.data?.modules.some((module) => module.qualname === "clock"));
  const query = useQuery({
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
  // A removed or disabled plugin must not leave cached zones visible in native placements.
  return { ...query, data: enabled ? query.data : null };
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

const offsetText = (minutes: number) => {
  if (minutes === 0) return t("status.yourTime");
  const hours = Math.abs(minutes) / 60;
  return `${minutes > 0 ? "+" : "−"}${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`;
};

/** `America/Los_Angeles` → `Los Angeles`. */
export const city = (zone: string) => (zone.split("/").at(-1) ?? zone).replaceAll("_", " ");

/** Whether this browser knows the IANA zone `zone`; formatting an unknown one throws. */
export function knownZone(zone: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/** How long the zones stay open after the pointer leaves, so it can cross to them. */
const CLOSE_DELAY = 200;

/** The clock, with the team's zones on hover, focus or a click (which pins them): each one's time in that place's
 *  convention and whether it's within working hours, and a link to edit them. */
function Clock({ now }: { now: Date }) {
  const zones = useZones();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const wrapper = useRef<HTMLSpanElement>(null);
  const panelId = useId();
  const pane = useRef<HTMLDivElement>(null);
  const visible = useFooterVisibility();
  const returningFocus = useRef(false);
  const [placement, setPlacement] = useState<{ left: number; top: number; maxHeight: number } | null>(null);
  const open = visible && (hovered || focused || pinned);
  useEffect(() => {
    if (!visible) {
      setHovered(false);
      setFocused(false);
      setPinned(false);
    }
  }, [visible]);
  useLayoutEffect(() => {
    if (!open || !pane.current || !wrapper.current) return;
    const popup = pane.current;
    const position = () => {
      if (!wrapper.current) return;
      const box = popup.getBoundingClientRect();
      const maxHeight = Math.max(1, window.innerHeight - 16);
      const next = {
        ...placePopover(
          wrapper.current.getBoundingClientRect(),
          { width: box.width, height: Math.min(box.height, maxHeight) },
          "above",
        ),
        maxHeight,
      };
      setPlacement((old) =>
        old?.left === next.left && old.top === next.top && old.maxHeight === next.maxHeight ? old : next,
      );
    };
    position();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(position);
    observer?.observe(popup);
    observer?.observe(wrapper.current);
    window.addEventListener("resize", position);
    window.addEventListener(FOOTER_RELOCATED, position);
    window.addEventListener("scroll", position, true);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", position);
      window.removeEventListener(FOOTER_RELOCATED, position);
      window.removeEventListener("scroll", position, true);
    };
  }, [open]);
  // The first zone is yours (`clock.zones` is ordered): the clock shows its time and the panel lists it first, with
  // the others after it. Unknown browser zones keep their place and show an explicit unavailable value.
  const known = zones.data ?? [];
  const primary = known[0]?.zone;
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const enter = () => {
    window.clearTimeout(timer.current);
    setHovered(true);
  };
  const leave = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setHovered(false), CLOSE_DELAY);
  };
  const close = () => {
    window.clearTimeout(timer.current);
    setHovered(false);
    setFocused(false);
    setPinned(false);
  };
  useEffect(() => {
    if (!pinned) return;
    const outside = (event: MouseEvent) => {
      if (!wrapper.current?.contains(event.target as Node) && !pane.current?.contains(event.target as Node))
        setPinned(false);
    };
    window.addEventListener("mousedown", outside);
    return () => window.removeEventListener("mousedown", outside);
  }, [pinned]);
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: hover and focus only show what the time button opens on click
    <span
      ref={wrapper}
      className="relative shrink-0"
      onMouseEnter={enter}
      onMouseLeave={leave}
      onFocus={() => {
        if (!returningFocus.current) setFocused(true);
      }}
      onBlur={(event) => {
        if (
          !wrapper.current?.contains(event.relatedTarget as Node | null) &&
          !pane.current?.contains(event.relatedTarget as Node | null)
        )
          setFocused(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation();
          close();
        }
      }}
    >
      <button
        type="button"
        onClick={() => setPinned((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-describedby={`${panelId}-hint`}
        className="rounded px-1 font-mono text-foreground/80 tabular-nums hover:bg-accent/60"
      >
        <time dateTime={now.toISOString()}>{primary && !knownZone(primary) ? "--:--" : zoneClock(now, primary)}</time>
        <span id={`${panelId}-hint`} className="sr-only">
          {t("status.zones")}
        </span>
      </button>
      {open &&
        createPortal(
          // biome-ignore lint/a11y/noStaticElementInteractions: hover and focus retain the pane opened by the clock button
          <div
            ref={pane}
            id={panelId}
            className="fixed z-(--layer-popover) overflow-y-auto"
            style={placement ?? { left: 0, top: 0, visibility: "hidden" }}
            onMouseEnter={enter}
            onMouseLeave={leave}
            onFocus={() => setFocused(true)}
            onBlur={(event) => {
              if (
                !pane.current?.contains(event.relatedTarget as Node | null) &&
                !wrapper.current?.contains(event.relatedTarget as Node | null)
              )
                setFocused(false);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                close();
                returningFocus.current = true;
                wrapper.current?.querySelector("button")?.focus();
                returningFocus.current = false;
              }
            }}
          >
            <section
              aria-label={t("status.zones")}
              className="w-80 rounded-xl border border-border bg-popover p-1.5 text-sm shadow-xl"
            >
              {known.length > 0 ? (
                // Primary first, then the others in the order of `clock.zones`; every time on the 24-hour clock.
                <ul>
                  {known.map((zone, index) => {
                    const at = knownZone(zone.zone) ? partsIn(zone.zone, now) : null;
                    const sameDay = !at || at.weekday === now.getDay();
                    const work = workingHours(zone, now);
                    return (
                      <li key={zone.zone} className="flex items-center gap-3 rounded-lg px-2.5 py-2">
                        <span className="min-w-0 flex-1">
                          <span className="flex items-baseline gap-2">
                            <span className="truncate text-foreground">{city(zone.zone)}</span>
                            {index === 0 ? (
                              <span className="rounded-md border border-primary/40 px-1.5 text-[11px] text-primary">
                                {t("settings.primary")}
                              </span>
                            ) : (
                              <span className="text-xs text-muted-foreground">
                                {at ? offsetText(at.offset) : zone.zone}
                              </span>
                            )}
                          </span>
                        </span>
                        <WorkStateIcon work={work} />
                        <span className="shrink-0 font-mono text-foreground tabular-nums">
                          {sameDay ? "" : `${at?.day} `}
                          {at ? zoneTime(now, zone.zone) : t("worldClock.unavailable")}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="px-2.5 py-1.5 text-muted-foreground">{t("status.noZones")}</p>
              )}
              <div className="mt-1 flex justify-end border-t border-border pt-1">
                <SettingsShortcut section={CLOCK_SETTINGS} label={t("status.editZones")} />
              </div>
            </section>
          </div>,
          document.body,
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

const time = (date: Date) => zoneTime(date);

/** "in 12 min", "in 2 h 5 min", or the time when it's tomorrow or later. */
function until(start: Date, now: Date): string {
  const minutes = Math.round((start.getTime() - now.getTime()) / 60_000);
  if (minutes < 1) return t("status.now");
  if (start.toDateString() !== now.toDateString()) return time(start);
  if (minutes < 60) return t("status.inMinutes", { minutes });
  return t("status.inHours", { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
}

const RELEASES = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/tag";

/** Keep the version pane focused on the installed release rather than repeating the full history. */
function installedReleaseNotes(changelog: string, version: string): string | null {
  const escaped = version.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const heading = new RegExp(`^##\\s+\\[?${escaped}\\]?(?:\\s|\\(|$).*?$`, "m");
  const match = heading.exec(changelog);
  if (!match) return null;
  const start = match.index + match[0].length;
  const next = /^##\s+/gm;
  next.lastIndex = start;
  const end = next.exec(changelog)?.index ?? changelog.length;
  return changelog.slice(start, end).trim() || null;
}

/** The installed version, its mascot, concise tagline and release link. */
function ReleaseNotes({ version }: { version: string }) {
  const changelog = useChangelog(false);
  const notes = typeof changelog.data === "string" ? installedReleaseNotes(changelog.data, version) : null;
  return (
    <div className="px-1.5 pb-1" aria-busy={changelog.isPending}>
      <section
        aria-label={t("status.about")}
        className="mb-2 flex items-center justify-between gap-2 border-b border-border pb-2"
      >
        <HeinzelAvatar label={t("app.name")} className="h-10 w-7 shrink-0" />
        <p className="min-w-0 flex-1 text-[10px] leading-tight font-medium">
          <span className="block">{t("status.tagline")}</span>
          <span className="block">{t("status.origin")}</span>
        </p>
        <a
          href={`${RELEASES}/v${version}`}
          target="_blank"
          rel="noreferrer"
          aria-label={t("status.release")}
          title={t("status.release")}
          className="flex size-7 shrink-0 items-center justify-center rounded-md hover:bg-accent"
        >
          <svg aria-hidden="true" viewBox="0 0 16 16" className="size-4 fill-current">
            <path d="M8 .2a7.8 7.8 0 0 0-2.47 15.2c.39.07.53-.17.53-.38v-1.48c-2.17.47-2.63-.92-2.63-.92-.36-.9-.87-1.14-.87-1.14-.71-.49.06-.48.06-.48.78.06 1.19.8 1.19.8.7 1.2 1.83.85 2.28.65.07-.5.27-.85.5-1.04-1.73-.2-3.55-.87-3.55-3.87 0-.85.3-1.54.8-2.08-.08-.2-.35-.99.08-2.05 0 0 .65-.2 2.14.79a7.44 7.44 0 0 1 3.9 0c1.49-1 2.14-.79 2.14-.79.43 1.06.16 1.85.08 2.05.5.54.8 1.23.8 2.08 0 3.01-1.82 3.67-3.56 3.87.28.24.53.71.53 1.43v2.18c0 .21.14.45.54.38A7.8 7.8 0 0 0 8 .2Z" />
          </svg>
          <span className="sr-only">{t("status.release")}</span>
        </a>
      </section>
      {notes && (
        <div className="max-h-64 overflow-y-auto pr-1 text-xs">
          <Prose>{notes}</Prose>
        </div>
      )}
    </div>
  );
}

/** The meeting now or next as an icon: green with its line and Join in the pane, or red when no calendar is connected. */
function MeetingItem({ now }: { now: Date }) {
  const agenda = useAgenda();
  const meeting = useShown("org:meeting", "footer");
  const current = agenda.data?.current;
  const next = agenda.data?.next;
  if (!meeting) return null;
  if (current)
    return (
      <FooterItem
        label={t("status.current", { title: current.title, end: time(new Date(current.end)) })}
        state="ok"
        icon={Video}
      >
        {current.conference_url && (
          <a
            href={current.conference_url}
            target="_blank"
            rel="noreferrer"
            className="block rounded-lg px-2.5 py-1.5 text-primary underline"
          >
            {t("status.join")}
          </a>
        )}
      </FooterItem>
    );
  if (next)
    return (
      <FooterItem
        label={t("status.next", { title: next.title, when: until(new Date(next.start), now) })}
        state="ok"
        icon={CalendarClock}
      />
    );
  if (agenda.data?.problem)
    return (
      <FooterItem label={t("status.noCalendar")} state="bad" icon={Calendar}>
        <p className="px-2.5 py-1.5 text-muted-foreground">{agenda.data.fix ?? agenda.data.problem}</p>
        <Link
          to={`?settings=${CONNECTORS}`}
          className="block rounded-lg border-t border-border px-2.5 pt-2 pb-1 text-xs underline hover:text-foreground"
        >
          {t("status.connectCalendar")}
        </Link>
      </FooterItem>
    );
  return null;
}

export function StatusBar() {
  const info = useInfo();
  const clockInstalled = Boolean(info.data?.modules.some((module) => module.qualname === "clock"));
  const clock = useShown("clock:primary-zone", "footer", clockInstalled);
  const now = useNow();
  return (
    <FooterLayout className="h-[60px] shrink-0 gap-2 overflow-visible border-t-[0.5px] border-foreground/20 px-4 text-ui text-muted-foreground">
      <FooterPart id="@version" pinned className="mr-auto">
        {info.data && (
          <FooterItem
            label={`v${info.data.version}`}
            state="off"
            text={`v${info.data.version}`}
            align="left"
            popoverClassName="w-[min(28rem,calc(100vw_-_2rem))]"
          >
            <ReleaseNotes version={info.data.version} />
          </FooterItem>
        )}
      </FooterPart>
      <FooterPart id="@updates" pinned priority={100} label={t("updates.title")}>
        <UpdateStatus />
      </FooterPart>
      <StatusItems slot="footer" except={NATIVE_FOOTER} measured />
      <FooterPart id="@resources" priority={60} label={t("resource.title")}>
        <ResourceStatus />
      </FooterPart>
      <FooterPart id="@heinzels" priority={70} label={t("nav.heinzelmaennchen")}>
        <DurableHeinzelStatus />
      </FooterPart>
      <FooterPart id="@health" priority={90} label={t("health.title")}>
        <HealthStatus />
      </FooterPart>
      <FooterPart id="@meeting" priority={30}>
        <MeetingItem now={now} />
      </FooterPart>
      <FooterPart id="@clock" priority={40} label={t("status.zones")}>
        {clockInstalled && clock && <Clock now={now} />}
      </FooterPart>
      <FooterMore />
    </FooterLayout>
  );
}
