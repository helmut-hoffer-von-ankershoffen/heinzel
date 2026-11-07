// The status bar under the screen: hz's version on the left; the meeting running now or the next
// one, and a clock, on the right, then what modules add (ADR 0056). The meeting comes from `hz org agenda` (the bound calendar), refreshed each minute.

import { useQuery } from "@tanstack/react-query";
import { Calendar } from "lucide-react";
import { useEffect, useState } from "react";
import { useChangelog, useInfo } from "@/api/queries";
import { token } from "@/api/token";
import { NATIVE_FOOTER, StatusItems, useShown } from "@/components/Contributions";
import { FooterItem } from "@/components/FooterItem";
import { FooterLayout, FooterMore, FooterPart } from "@/components/FooterLayout";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { Prose } from "@/components/Prose";
import { t } from "@/i18n";
import { zoneTime } from "@/lib/zoneTime";
import { AutoUpdate } from "@/updates/AutoUpdate";
import { DurableHeinzelStatus } from "./DurableHeinzelStatus";
import { HealthStatus } from "./HealthStatus";
import { PluginsPending } from "./PluginsPending";
import { UpdateStatus } from "./UpdateStatus";

type Meeting = { title: string; start: string; end: string; conference_url?: string | null };
type Agenda = {
  timezone?: string | null;
  current: Meeting | null;
  next: Meeting | null;
  problem: string | null;
  fix: string | null;
};

/** `GET /api/org/agenda`; answers 404 until the server has it, which shows as no meeting. */
function useAgenda() {
  return useQuery({
    queryKey: ["org", "agenda"],
    queryFn: async (): Promise<Agenda> => {
      const request = new Request(new URL("/api/org/agenda", window.location.origin), {
        headers: { Authorization: `Bearer ${token() ?? ""}` },
      });
      const response = await fetch(request);
      if (!response.ok) throw new Error("Calendar unavailable");
      return (await response.json()) as Agenda;
    },
    refetchInterval: 60_000,
    retry: false,
  });
}

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tick = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(tick);
  }, []);
  return now;
}

const time = (date: Date, zone?: string | null) => zoneTime(date, zone ?? undefined);

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

/** Two fixed half-rings preserve the icon footprint and distinguish elapsed time from the next start. */
function MeetingIcon({ elapsed, approach }: { elapsed: number | null; approach: number | null }) {
  return (
    <span className="relative grid size-4 place-items-center" aria-hidden>
      <Calendar className="size-3.5" />
      <svg
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-1/2 size-5 -translate-x-1/2 -translate-y-1/2"
        viewBox="0 0 24 24"
        fill="none"
      >
        {elapsed !== null && (
          <>
            <path d="M 2 12 A 10 10 0 0 1 22 12" stroke="currentColor" className="opacity-25" />
            <path
              d="M 2 12 A 10 10 0 0 1 22 12"
              pathLength="100"
              stroke="currentColor"
              strokeDasharray={`${elapsed} 100`}
              strokeWidth="1.5"
              className="text-ok"
            />
          </>
        )}
        {approach !== null && (
          <>
            <path d="M 22 12 A 10 10 0 0 1 2 12" stroke="currentColor" className="opacity-25" />
            <path
              d="M 22 12 A 10 10 0 0 1 2 12"
              pathLength="100"
              stroke="currentColor"
              strokeDasharray={`${approach} 100`}
              strokeWidth="1.5"
              className="text-primary"
            />
          </>
        )}
      </svg>
    </span>
  );
}

/** The current and upcoming meetings share one compact entry to the calendar panel. */
function MeetingItem({ now, onOpen }: { now: Date; onOpen: () => void }) {
  const agenda = useAgenda();
  const meeting = useShown("org:meeting", "footer");
  const current = agenda.data?.current;
  const next = agenda.data?.next;
  const running = [current, next].filter((event): event is Meeting =>
    Boolean(event && new Date(event.start) <= now && now < new Date(event.end)),
  );
  const active = running.sort((a, b) => Date.parse(b.start) - Date.parse(a.start))[0];
  const upcoming = next && new Date(next.start) > now ? next : null;
  const boundary = [current?.end, next?.start]
    .filter((value): value is string => Boolean(value))
    .map(Date.parse)
    .filter((value) => value > agenda.dataUpdatedAt && value <= now.getTime());
  useEffect(() => {
    if (boundary.length) void agenda.refetch();
  }, [agenda.refetch, boundary.length]);
  if (!meeting) return null;
  const unavailable = agenda.isError || Boolean(agenda.data?.problem);
  const elapsed = active
    ? (100 * (now.getTime() - Date.parse(active.start))) / (Date.parse(active.end) - Date.parse(active.start))
    : null;
  const nextMinutes = upcoming ? Math.ceil((Date.parse(upcoming.start) - now.getTime()) / 60_000) : null;
  const approach = nextMinutes === null ? null : Math.max(0, (100 * (60 - nextMinutes)) / 60);
  const showProgress = !unavailable && (elapsed !== null || approach !== null);
  return (
    <FooterItem
      label="Calendar"
      icon={showProgress ? undefined : Calendar}
      content={showProgress ? <MeetingIcon elapsed={elapsed} approach={approach} /> : undefined}
      state={unavailable ? "bad" : "off"}
      paneName="Calendar summary"
      hideHeading
    >
      <div className="space-y-2 px-1.5 py-1">
        <p className="font-medium">Calendar</p>
        {agenda.isPending ? (
          <p>Loading calendar…</p>
        ) : agenda.isError ? (
          <p>Calendar unavailable. Try again later.</p>
        ) : agenda.data?.problem ? (
          <p>{agenda.data.problem}</p>
        ) : (
          <>
            {active ? (
              <p className="break-words">
                In {active.title} · {Math.max(1, Math.ceil((Date.parse(active.end) - now.getTime()) / 60_000))} min
                remaining
                {" · Ends at "}
                <time className="inline-block" dateTime={active.end}>
                  {time(new Date(active.end), agenda.data?.timezone)}
                </time>
              </p>
            ) : (
              <p>Not in a meeting</p>
            )}
            {upcoming ? (
              <p className="break-words">
                Next: {upcoming.title} ·{" "}
                <time className="inline-block" dateTime={upcoming.start}>
                  {new Intl.DateTimeFormat(undefined, {
                    weekday: "short",
                    month: "short",
                    day: "numeric",
                    timeZone: agenda.data?.timezone ?? undefined,
                  }).format(new Date(upcoming.start))}{" "}
                  at {time(new Date(upcoming.start), agenda.data?.timezone)}
                </time>
                {" · in "}
                {nextMinutes} min
              </p>
            ) : (
              <p>None through tomorrow</p>
            )}
          </>
        )}
        {!unavailable && (elapsed !== null || approach !== null) && (
          <p className="text-xs text-muted-foreground">Top arc: meeting elapsed. Bottom arc: starts within an hour.</p>
        )}
        <button type="button" className="min-h-10 text-primary underline" onClick={onOpen}>
          Open calendar
        </button>
      </div>
    </FooterItem>
  );
}

export function StatusBar({ onOpenCalendar = () => {} }: { onOpenCalendar?: () => void }) {
  const info = useInfo();
  const now = useNow();
  return (
    <>
      <AutoUpdate />
      <FooterLayout className="h-12 shrink-0 gap-2 overflow-visible border-t-[0.5px] border-foreground/20 px-4 text-ui text-muted-foreground">
        <FooterPart id="@version" priority={950} className="mr-auto">
          {info.data && (
            <FooterItem
              label={`v${info.data.version}`}
              state="off"
              text={`v${info.data.version}`}
              align="left"
              popoverClassName="w-[min(28rem,calc(var(--hz-viewport-width,100vw)_-_2rem))]"
            >
              <ReleaseNotes version={info.data.version} />
            </FooterItem>
          )}
        </FooterPart>
        <FooterPart id="@plugins-pending" priority={110} label={t("pluginsPending.title")}>
          <PluginsPending />
        </FooterPart>
        <FooterPart id="@updates" priority={100} label={t("updates.title")}>
          <UpdateStatus />
        </FooterPart>
        <FooterPart id="@heinzels" priority={70} label={t("nav.heinzelmaennchen")}>
          <DurableHeinzelStatus />
        </FooterPart>
        <FooterPart id="@health" priority={90} label={t("health.title")}>
          <HealthStatus />
        </FooterPart>
        {/* Even the generic error promotion keeps this contribution below the version priority. */}
        <FooterPart id="@meeting" priority={725}>
          <MeetingItem now={now} onOpen={onOpenCalendar} />
        </FooterPart>
        <FooterMore />
        <StatusItems slot="footer" except={NATIVE_FOOTER} measured />
      </FooterLayout>
    </>
  );
}
