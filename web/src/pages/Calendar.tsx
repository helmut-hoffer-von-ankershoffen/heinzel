// `hz calendar` and `hz calendar week`: your events with the link to join, your answer and the guests. Answering
// asks the server what it would do first and runs only after Confirm, as `hz calendar respond ... --confirm` does.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { api, type SchemaCalendarChange, type SchemaEvent, unwrap } from "@/api/client";
import { Loaded, Page } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Badge, Card } from "@/components/ui/card";
import { type MessageKey, t } from "@/i18n";
import { cn } from "@/lib/utils";

type Answer = "accepted" | "declined" | "tentative";
const ANSWERS: Answer[] = ["accepted", "tentative", "declined"];
const answerLabel: Record<string, MessageKey> = {
  accepted: "calendar.accepted",
  tentative: "calendar.tentative",
  declined: "calendar.declined",
  pending: "calendar.pending",
};

/** Only the conferencing services' own https links are shown; the adapter checks them too (ADR 0028). */
export function joinLink(url: string | null | undefined): string | null {
  return url?.startsWith("https://") ? url : null;
}

const time = new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit" });
const dayName = new Intl.DateTimeFormat("en", { weekday: "long", day: "numeric", month: "long" });

function byDay(events: SchemaEvent[]): [string, SchemaEvent[]][] {
  const days = new Map<string, SchemaEvent[]>();
  for (const event of events) {
    const day = dayName.format(new Date(event.start));
    days.set(day, [...(days.get(day) ?? []), event]);
  }
  return [...days];
}

function useEvents(week: boolean) {
  return useQuery({
    queryKey: ["calendar", week ? "week" : "today"],
    queryFn: () => unwrap(week ? api.GET("/calendar/week") : api.GET("/calendar")),
  });
}

function useCountdown() {
  return useQuery({
    queryKey: ["calendar", "now"],
    queryFn: () => unwrap(api.GET("/calendar/now")),
    refetchInterval: 60_000,
  });
}

function useRespond() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (variables: { event: string; answer: Answer; confirm: boolean }) =>
      unwrap(api.POST("/calendar/respond", { params: { query: variables } })),
    onSuccess: (change) => {
      if (change.done) void client.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
}

function EventRow({
  event,
  focused,
  onAnswer,
}: {
  event: SchemaEvent;
  focused: boolean;
  onAnswer: (answer: Answer) => void;
}) {
  const join = joinLink(event.conference_url);
  const guests = (event.attendees ?? []).map((person) => person.name ?? person.email ?? "").filter(Boolean);
  return (
    <li className={cn("flex flex-wrap items-start gap-x-4 gap-y-1 py-3", focused && "rounded-lg bg-accent px-2")}>
      <span className="w-28 shrink-0 text-sm text-muted-foreground tabular-nums">
        {event.all_day
          ? t("calendar.allDay")
          : `${time.format(new Date(event.start))}–${time.format(new Date(event.end))}`}
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium">{event.title}</p>
        {event.location ? <p className="text-sm text-muted-foreground">{event.location}</p> : null}
        {guests.length > 0 ? (
          <p className="text-sm text-muted-foreground">{t("calendar.with", { names: guests.join(", ") })}</p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {event.response ? <Badge>{t(answerLabel[event.response] ?? "calendar.pending")}</Badge> : null}
        {event.organized_by_user ? <Badge tone="primary">{t("calendar.yours")}</Badge> : null}
        {join ? (
          <a href={join} target="_blank" rel="noopener noreferrer" className="text-sm text-primary underline">
            {t("calendar.join")}
          </a>
        ) : null}
        {event.response
          ? ANSWERS.filter((answer) => answer !== event.response).map((answer) => (
              <Button key={answer} variant="outline" size="sm" onClick={() => onAnswer(answer)}>
                {t(`calendar.answer.${answer}`)}
              </Button>
            ))
          : null}
      </div>
    </li>
  );
}

function Confirmation({
  change,
  busy,
  onConfirm,
  onCancel,
}: {
  change: SchemaCalendarChange;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Card role="alertdialog" aria-label={t("calendar.confirmTitle")}>
      <p className="mb-3">{change.summary}</p>
      <div className="flex gap-2">
        <Button onClick={onConfirm} disabled={busy}>
          {t("calendar.confirm")}
        </Button>
        <Button variant="outline" onClick={onCancel} disabled={busy}>
          {t("calendar.cancel")}
        </Button>
      </div>
    </Card>
  );
}

export function Calendar() {
  const [params, setParams] = useSearchParams();
  const week = params.get("view") === "week";
  const focus = params.get("event");
  const events = useEvents(week);
  const countdown = useCountdown();
  const respond = useRespond();
  const [pending, setPending] = useState<{ event: string; answer: Answer; change: SchemaCalendarChange } | null>(null);
  const ask = (event: string, answer: Answer) =>
    respond.mutate({ event, answer, confirm: false }, { onSuccess: (change) => setPending({ event, answer, change }) });
  const confirm = () => {
    if (!pending) return;
    respond.mutate(
      { event: pending.event, answer: pending.answer, confirm: true },
      { onSuccess: () => setPending(null) },
    );
  };
  return (
    <Page
      title={t("calendar.title")}
      intro={countdown.data?.line ?? t("calendar.intro")}
      actions={
        <fieldset className="flex gap-1" aria-label={t("calendar.view")}>
          {(["today", "week"] as const).map((view) => (
            <Button
              key={view}
              variant={(view === "week") === week ? "default" : "outline"}
              aria-pressed={(view === "week") === week}
              onClick={() => setParams(view === "week" ? { view } : {})}
            >
              {t(`calendar.${view}`)}
            </Button>
          ))}
        </fieldset>
      }
    >
      {pending ? (
        <Confirmation
          change={pending.change}
          busy={respond.isPending}
          onConfirm={confirm}
          onCancel={() => setPending(null)}
        />
      ) : null}
      {respond.error ? <p className="text-bad">{respond.error.message}</p> : null}
      <Loaded query={events}>
        {(agenda) =>
          (agenda.events ?? []).length === 0 ? (
            <p className="text-muted-foreground">{t("calendar.empty")}</p>
          ) : (
            byDay(agenda.events ?? []).map(([day, list]) => (
              <section key={day} aria-label={day}>
                <h2 className="mt-4 text-sm font-medium text-muted-foreground">{day}</h2>
                <ul className="divide-y divide-border">
                  {list.map((event) => (
                    <EventRow
                      key={event.id}
                      event={event}
                      focused={event.id === focus}
                      onAnswer={(answer) => ask(event.id, answer)}
                    />
                  ))}
                </ul>
              </section>
            ))
          )
        }
      </Loaded>
    </Page>
  );
}
