// Home is the chat (ADR 0045): a "Today" strip of what needs you on top, the conversation below, the composer pinned
// at the bottom. A card opens its screen, starts a chat about it, or goes away; `?card=<id>` focuses one (a
// notification's link), `?chat-context=event:<id>` opens the chat about that event. Other screens have the same chat
// in their right sidebar.
import { MessageSquarePlus, Plus, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router";
import { Composer, Conversation, Costs } from "@/chat/Conversation";
import { type TodayCard, useToday } from "@/chat/today";
import { useChat } from "@/chat/useChat";
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
  const chat = useChat();
  const today = useToday();
  const [params] = useSearchParams();
  const focused = params.get("card");
  const input = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const count = (chat.session?.messages?.length ?? 0) + (chat.live ? 1 : 0);

  useEffect(() => input.current?.focus(), []);
  // Keep the newest message in view as the conversation grows or the answer streams.
  // biome-ignore lint/correctness/useExhaustiveDependencies: scrolls when the conversation changes
  useEffect(() => end.current?.scrollIntoView?.({ block: "end" }), [count, chat.live?.text]);

  const ask = async (card: TodayCard) => {
    await chat.openAbout(card.context, card.detail ? `${card.title}: ${card.detail}` : card.title);
    input.current?.focus();
  };

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col">
      <section aria-labelledby="today" className="px-4 pt-6 sm:px-6">
        <div className="flex items-center gap-2">
          <h1 id="today" className="text-2xl font-semibold">
            {t("today.title")}
          </h1>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            onClick={() => {
              chat.fresh();
              input.current?.focus();
            }}
            disabled={chat.live != null || !chat.session}
          >
            <Plus aria-hidden />
            {t("chat.new")}
          </Button>
        </div>
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
      <section aria-label={t("chat.title")} className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        {chat.session?.context && (
          <p className="mb-3 text-xs text-muted-foreground">
            {t("chat.context", { about: `${chat.session.context.kind} ${chat.session.context.ref}`.trim() })}
          </p>
        )}
        <Conversation chat={chat} empty={t("today.empty")} />
        <div ref={end} />
      </section>
      <div className="sticky bottom-0 border-t border-border bg-background px-4 py-3 sm:px-6">
        <Composer chat={chat} context="route:/" inputRef={input} />
        <Costs chat={chat} className="mt-2" />
      </div>
    </div>
  );
}
