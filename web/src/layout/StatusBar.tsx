// The status bar under the screen: hz's version on the left; the meeting running now or the next
// one, and a clock, on the right, then what modules add (ADR 0056). The meeting comes from `hz org agenda` (the bound calendar), refreshed each minute.

import { useQuery } from "@tanstack/react-query";
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
import { CalendarGlyph } from "./CalendarGlyph";
import { CalendarSummary } from "./CalendarSummary";
import { type AgendaData, glyphModel } from "./calendarModel";
import { DurableHeinzelStatus } from "./DurableHeinzelStatus";
import { HealthStatus } from "./HealthStatus";
import { PluginsPending } from "./PluginsPending";
import { UpdateStatus } from "./UpdateStatus";

type Meeting = AgendaData["current"] & object;
type Agenda = AgendaData;

/** `GET /api/org/agenda`; answers 404 until the server has it, which shows as no meeting. */
function useAgenda() {
  return useQuery({
    queryKey: ["org", "agenda"],
    queryFn: async ({ signal }): Promise<Agenda> => {
      const request = new Request(new URL("/api/org/agenda", window.location.origin), {
        signal,
        cache: "no-store",
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

/** The current and upcoming meetings share one compact entry: the Ledger glyph and a read-only pane. */
function MeetingItem({ now, onOpen }: { now: Date; onOpen: () => void }) {
  const agenda = useAgenda();
  const meeting = useShown("org:meeting", "footer");
  const ms = now.getTime();
  const boundary = [agenda.data?.current?.end, agenda.data?.next?.start]
    .filter((value): value is string => Boolean(value))
    .map(Date.parse)
    .filter((value) => value > agenda.dataUpdatedAt && value <= ms);
  useEffect(() => {
    if (boundary.length) void agenda.refetch();
  }, [agenda.refetch, boundary.length]);
  if (!meeting) return null;
  const model = glyphModel({ agenda: agenda.data, failed: agenda.isError, pending: agenda.isPending, now: ms });
  const zone = agenda.data?.timezone;
  const events = [agenda.data?.current, agenda.data?.next].filter((e): e is Meeting => Boolean(e));
  const active =
    events
      .filter((e) => Date.parse(e.start) <= ms && ms < Date.parse(e.end))
      .sort((a, b) => Date.parse(b.start) - Date.parse(a.start))[0] ?? null;
  const next = agenda.data?.next;
  const upcoming = next && Date.parse(next.start) > ms ? next : null;
  const unavailable = model.kind === "unknown";
  return (
    // Opening the calendar closes this pane, so it never stays over the panel it opened.
    <FooterItem
      label="Calendar"
      triggerLabel={`Calendar: ${model.description}`}
      state={unavailable ? "bad" : "off"}
      content={<CalendarGlyph model={model} />}
      className="px-0"
      paneName="Calendar summary"
      hideHeading
      popoverClassName="w-[min(24rem,calc(var(--hz-viewport-width,100vw)_-_2rem))]"
    >
      {(close) => {
        const open = () => {
          close();
          onOpen();
        };
        return agenda.isPending ? (
          <p className="p-3">Loading calendar…</p>
        ) : agenda.isError ? (
          <p className="p-3">Calendar unavailable. Try again later.</p>
        ) : agenda.data?.problem ? (
          <div className="space-y-2 p-3">
            <p className="text-[17px] font-semibold">Calendar</p>
            <p>{agenda.data.problem}</p>
            <button type="button" className="min-h-11 text-primary underline" onClick={open} aria-label="Open calendar">
              Open calendar
            </button>
          </div>
        ) : (
          <CalendarSummary
            model={model}
            active={active}
            upcoming={upcoming}
            now={ms}
            zone={zone}
            clock={(iso) => time(new Date(iso), zone)}
            onOpen={open}
          />
        );
      }}
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
