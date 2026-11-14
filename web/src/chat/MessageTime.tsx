// Times on demand (canvas boards Options and Timeline note): never a time beside every message. Each message carries
// its `<time>` for screen readers; the same time shows above the message on mouse hover, on keyboard focus or after a
// tap, and Escape hides it again. The messages are one tab stop (the newest), the arrow keys walk the others, so a
// long conversation never costs hundreds of Tab presses. Day headings ("Today", "Yesterday", "Thursday 13 November")
// say which day a run of messages belongs to; their words come from `lib/dayTime`.
import { type KeyboardEvent, type MouseEvent, type ReactNode, useId, useRef, useState } from "react";
import { dayHeading, fullMoment, timeOfDay, useNow } from "@/lib/dayTime";
import { cn } from "@/lib/utils";

export function DayHeading({ at, zone }: { at: string; zone?: string }) {
  const now = useNow(zone);
  return (
    <h3 data-day-heading="" className="pt-2 text-center text-xs font-medium text-muted-foreground">
      {dayHeading(at, now, zone)}
    </h3>
  );
}

const ROW = "[data-message-time-row]";

/** Up and Down walk from one message to the next; the rest of the keys are the page's. */
function walk(event: KeyboardEvent<HTMLElement>) {
  if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
  const rows = [...(event.currentTarget.closest("[data-message-speaker]")?.parentElement?.querySelectorAll(ROW) ?? [])];
  const next = rows[rows.indexOf(event.currentTarget) + (event.key === "ArrowDown" ? 1 : -1)];
  if (next instanceof HTMLElement) {
    event.preventDefault();
    next.focus();
  }
}

export function MessageTime({
  at,
  own,
  tabbable = true,
  zone,
  children,
}: {
  at: string;
  own: boolean;
  /** The one message the Tab key reaches (the newest); the others are reached with the arrow keys. */
  tabbable?: boolean;
  zone?: string;
  children: ReactNode;
}) {
  const id = useId();
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [tapped, setTapped] = useState(false);
  // A press with a pointer focuses the row too; the tap owns that case, so focus alone only counts for the keyboard.
  const pressed = useRef(false);
  const shown = hover || focus || tapped;
  const toggle = (event: MouseEvent<HTMLElement>) => {
    pressed.current = false;
    const target = event.target instanceof Element ? event.target : null;
    const selection = window.getSelection();
    if (target?.closest("a, button, input, summary, textarea") || (selection && !selection.isCollapsed)) return;
    setTapped((value) => !value);
  };
  return (
    <article
      tabIndex={tabbable ? 0 : -1}
      aria-describedby={id}
      data-message-time-row=""
      className="relative min-h-11 rounded-lg pt-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onPointerEnter={(event) => event.pointerType === "mouse" && setHover(true)}
      onPointerLeave={() => setHover(false)}
      onPointerDown={() => {
        pressed.current = true;
      }}
      onFocus={() => setFocus(!pressed.current)}
      onBlur={() => {
        setFocus(false);
        setTapped(false);
      }}
      onClick={toggle}
      onKeyDown={(event) => {
        if (event.key === "Escape" && shown) {
          setHover(false);
          setTapped(false);
          setFocus(false);
          event.stopPropagation();
          return;
        }
        walk(event);
      }}
    >
      <time id={id} dateTime={at} className="sr-only">
        {fullMoment(at, zone)}
      </time>
      {shown && (
        <span
          aria-hidden="true"
          data-message-time=""
          className={cn(
            "pointer-events-none absolute top-0 h-4 text-xs leading-4 text-muted-foreground",
            own ? "right-0" : "left-0",
          )}
        >
          {timeOfDay(at, zone)}
        </span>
      )}
      {children}
    </article>
  );
}
