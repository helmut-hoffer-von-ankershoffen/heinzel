// Home is "Today" (ADR 0045): cards of what needs you. The chat is the shell's panel, beside Home when the window is
// wide and below it when narrow, as on every screen. A card opens its screen, starts a chat about it, or goes away;
// `?card=<id>` focuses one (a notification's link), `?chat-context=event:<id>` opens the chat about that event.
import { MessageSquarePlus, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router";
import { useSharedChat } from "@/chat/ChatContext";
import { type TodayCard, useToday } from "@/chat/today";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

const TONES: Record<TodayCard["tone"], string> = {
  bad: "border-bad",
  warn: "border-warn",
  info: "border-border",
};

function Card({
  card,
  focused,
  busy,
  onAsk,
  onDismiss,
}: {
  card: TodayCard;
  focused: boolean;
  busy: boolean;
  onAsk: () => void;
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
        "flex w-72 shrink-0 snap-start flex-col gap-2 rounded-lg border-l-4 bg-card p-3 text-sm shadow-sm",
        TONES[card.tone],
        focused && "ring-2 ring-ring",
      )}
    >
      <h3 className="font-semibold">{card.title}</h3>
      {card.detail && <p className="line-clamp-2 text-xs text-muted-foreground">{card.detail}</p>}
      <div className="mt-auto flex flex-wrap gap-1">
        <Button asChild size="sm" variant="outline">
          <Link to={card.route}>{t("today.open")}</Link>
        </Button>
        <Button size="sm" variant="outline" onClick={onAsk} disabled={busy}>
          <MessageSquarePlus aria-hidden />
          {t("today.ask")}
        </Button>
        <Button
          size="icon"
          variant="ghost"
          className="ml-auto size-8"
          onClick={onDismiss}
          aria-label={t("today.dismiss", { title: card.title })}
          title={t("today.dismiss", { title: card.title })}
        >
          <X aria-hidden />
        </Button>
      </div>
    </li>
  );
}

export function Home() {
  const { chat, focus } = useSharedChat();
  const today = useToday();
  const [params] = useSearchParams();
  const focused = params.get("card");
  // Home is where you talk to your Heinzelmännchen: the composer has the focus when it opens.
  // biome-ignore lint/correctness/useExhaustiveDependencies: once, when Home opens
  useEffect(() => focus(), []);

  const ask = async (card: TodayCard) => {
    await chat.openAbout(card.context, card.detail ? `${card.title}: ${card.detail}` : card.title);
    focus();
  };

  return (
    <div className="mx-auto max-w-3xl">
      <section aria-labelledby="today" className="px-4 pt-6 pb-4 sm:px-6">
        <h1 id="today" className="text-2xl font-semibold">
          {t("today.title")}
        </h1>
        {today.cards.length ? (
          <ul className="-mx-1 mt-3 flex snap-x gap-3 overflow-x-auto px-1 pb-2">
            {today.cards.map((card) => (
              <Card
                key={card.id}
                card={card}
                focused={focused === card.id}
                busy={chat.live != null}
                onAsk={() => void ask(card)}
                onDismiss={() => today.dismiss(card.id)}
              />
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">{today.loading ? t("state.loading") : t("today.quiet")}</p>
        )}
      </section>
    </div>
  );
}
