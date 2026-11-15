// Home is "Today" (ADR 0045): cards of what needs you. The chat is the shell's panel, beside Home when the window is
// wide and below it when narrow, as on every screen. A card opens its screen, asks your default Heinzel about it ("Ask
// <Name>": typed into its composer, never sent), or goes away; `?card=<id>` focuses one (a notification's link).

import { ArrowUpRight, MessageSquarePlus, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router";
import { type AskHeinzel, useAskHeinzel } from "@/chat/askHeinzel";
import { type TodayCard, useToday } from "@/chat/today";
import { TodayWidgets } from "@/components/Contributions";
import { TodayHiddenWidgets } from "@/components/HiddenWidgets";
import { PaneAction, PaneActions, PaneChrome } from "@/components/PaneActions";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { TodayNotifications } from "@/notifications/TodayNotifications";

const TONES: Record<TodayCard["tone"], string> = {
  bad: "border-bad",
  warn: "border-warn",
  info: "border-border",
};

function Card({
  card,
  focused,
  ask,
  onDismiss,
}: {
  card: TodayCard;
  focused: boolean;
  ask: AskHeinzel | null;
  onDismiss: () => void;
}) {
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [focused]);
  return (
    <li
      ref={ref}
      aria-current={focused || undefined}
      className={cn(
        "relative flex min-w-0 flex-col gap-2 rounded-lg border-l-4 bg-card p-3 text-sm shadow-sm",
        TONES[card.tone],
        focused && "ring-2 ring-ring",
      )}
    >
      <PaneChrome>
        <PaneAction ghost icon={X} onClick={onDismiss}>
          {t("today.dismiss", { title: card.title })}
        </PaneAction>
      </PaneChrome>
      <h3 className="pr-10 font-semibold">{card.title}</h3>
      {card.detail && <p className="line-clamp-2 text-xs text-muted-foreground">{card.detail}</p>}
      <PaneActions className="mt-auto">
        <PaneAction key="open" icon={ArrowUpRight} to={card.route}>
          {t("today.open")}
        </PaneAction>
        {ask && (
          <PaneAction
            key="ask"
            icon={MessageSquarePlus}
            onClick={() => ask.ask(card.detail ? `${card.title}: ${card.detail}` : card.title)}
          >
            {t("today.askHeinzel", { name: ask.name })}
          </PaneAction>
        )}
      </PaneActions>
    </li>
  );
}

export function Home() {
  const today = useToday();
  const [params] = useSearchParams();
  const focused = params.get("card");
  const ask = useAskHeinzel();

  return (
    <div className="mx-auto max-w-6xl">
      <section aria-labelledby="today" className="@container px-4 pt-3 pb-4 sm:px-6">
        {/* The header's screen selector already shows "Today"; the heading stays for screen readers and landmarks. */}
        <h1 id="today" className="sr-only">
          {t("today.title")}
        </h1>
        <div className="mb-1 flex empty:hidden">
          <TodayHiddenWidgets />
        </div>
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(min(15rem,100%),1fr))] gap-3 empty:hidden">
          <TodayNotifications />
          {today.cards.map((card) => (
            <Card
              key={card.id}
              card={card}
              focused={focused === card.id}
              ask={ask}
              onDismiss={() => today.dismiss(card.id)}
            />
          ))}
          <TodayWidgets ask={ask} />
        </ul>
        {today.cards.length ? null : (
          <p className="mt-2 text-sm text-muted-foreground">{today.loading ? t("state.loading") : t("today.quiet")}</p>
        )}
      </section>
    </div>
  );
}
