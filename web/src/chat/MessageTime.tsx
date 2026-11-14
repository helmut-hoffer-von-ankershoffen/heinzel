// Times on demand (flag `one_chat`, canvas boards Options and Timeline note): never a time beside every message.
// Each message carries its `<time>` for screen readers; the same time shows above the message on hover, on keyboard
// focus or after a tap, and Escape hides it again. Day headings ("Today", "Yesterday", "Thursday 13 November") say
// which day a run of messages belongs to.
import { type ReactNode, useState } from "react";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

const clock = new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const weekdayDate = new Intl.DateTimeFormat("en", { weekday: "long", day: "numeric", month: "long" });
const weekdayDateYear = new Intl.DateTimeFormat("en", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
});
const full = new Intl.DateTimeFormat("en", { dateStyle: "full", timeStyle: "short", hourCycle: "h23" });

/** The person's calendar day of an instant, as one comparable key (their own time zone). */
export function dayKey(at: string): string {
  const date = new Date(at);
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

/** "Today", "Yesterday", else "Thursday 13 November" (with the year when it is not this year). */
export function dayHeading(at: string, now: Date = new Date()): string {
  const key = dayKey(at);
  if (key === dayKey(now.toISOString())) return t("oneChat.today");
  const before = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1, 12);
  if (key === dayKey(before.toISOString())) return t("oneChat.yesterday");
  const date = new Date(at);
  const parts = (date.getFullYear() === now.getFullYear() ? weekdayDate : weekdayDateYear).formatToParts(date);
  const pick = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  const tail = date.getFullYear() === now.getFullYear() ? "" : ` ${pick("year")}`;
  return `${pick("weekday")} ${pick("day")} ${pick("month")}${tail}`;
}

/** True when `at` starts a new day after `previous` (or there is no previous message). */
export function startsDay(at: string, previous?: string): boolean {
  return previous === undefined || dayKey(at) !== dayKey(previous);
}

export function DayHeading({ at }: { at: string }) {
  return (
    <h3 data-day-heading="" className="pt-2 text-center text-xs font-medium text-muted-foreground">
      {dayHeading(at)}
    </h3>
  );
}

export function MessageTime({ at, own, children }: { at: string; own: boolean; children: ReactNode }) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [tapped, setTapped] = useState(false);
  const shown = hover || focus || tapped;
  const time = new Date(at);
  return (
    <article
      // biome-ignore lint/a11y/noNoninteractiveTabindex: the row is the keyboard way to the time (canvas Options).
      tabIndex={0}
      data-message-time-row=""
      className="relative min-h-11 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocus={() => setFocus(true)}
      onBlur={() => {
        setFocus(false);
        setTapped(false);
      }}
      onClick={() => setTapped((value) => !value)}
      onKeyDown={(event) => {
        if (event.key === "Escape" && shown) {
          setHover(false);
          setTapped(false);
          setFocus(false);
          event.stopPropagation();
        }
      }}
    >
      <time dateTime={at} className="sr-only">
        {full.format(time)}
      </time>
      {shown && (
        <span
          aria-hidden="true"
          data-message-time=""
          className={cn(
            "pointer-events-none absolute -top-5 z-10 rounded bg-background px-1 text-xs text-muted-foreground",
            own ? "right-0" : "left-0",
          )}
        >
          {clock.format(time)}
        </span>
      )}
      {children}
    </article>
  );
}
