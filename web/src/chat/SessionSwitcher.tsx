// Whose conversation the chat shows: its title with a chevron that opens a searchable list of your Heinzelmännchen
// (`hz heinzel list`), each with its own conversation, then "Ask hz" for questions that belong to none of them, and the
// earlier ones. "New Heinzelmännchen" starts a conversation that ends in a contract you approve (ADR 0049, 0051).
//
// A bridge until the durable sessions of ADR 0049 exist: a Heinzelmännchen's conversation is the newest chat session
// opened about its contract (`contract:<namespace>:<name>`); choosing one with none yet opens it.
import { Check, ChevronDown, MessageCircleQuestion, Plus, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useChatSessions } from "@/api/chat";
import type { SchemaChatSession, SchemaChatSummary, SchemaHeinzelSummary } from "@/api/client";
import { useHeinzelmaennchen } from "@/api/queries";
import { format, type MessageKey, t } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Chat } from "./useChat";

/** One row of the switcher: a Heinzelmännchen, Ask hz, or an earlier question. */
export type SwitcherItem =
  | { kind: "heinzel"; id: string; title: string; state: SchemaHeinzelSummary["state"]; session?: string }
  | { kind: "ask"; id: "ask"; title: string }
  | { kind: "session"; id: string; title: string; updated: string; spent: number; pending: number; messages: number };

export type SwitcherGroup = { label: string; items: SwitcherItem[] };

/** What "New Heinzelmännchen" opens the chat about: the agent asks, then proposes `heinzel_new` for you to confirm. */
export const NEW_HEINZEL =
  "text:the person wants a new Heinzelmännchen. They name it and say what it should keep true, or pick a template from heinzel_playbooks; heinzel_new then creates a starter or the template once they confirm, and they edit the contract from there.";

const qualified = (heinzel: SchemaHeinzelSummary) => `${heinzel.namespace}:${heinzel.name}`;

/** The Heinzelmännchen a chat session belongs to, by the contract it was opened about; none for other sessions. */
export const heinzelOf = (context: SchemaChatSession["context"] | undefined): string | null =>
  context?.kind === "contract" && context.ref ? context.ref : null;

/** The groups in the order shown: your Heinzelmännchen, Ask hz, then earlier questions; empty groups go. */
export function switcherGroups(
  sessions: SchemaChatSummary[],
  heinzelmaennchen: SchemaHeinzelSummary[],
  needle: string,
): SwitcherGroup[] {
  const match = (text: string) => !needle || text.toLowerCase().includes(needle.toLowerCase());
  const mine = new Set(heinzelmaennchen.map(qualified));
  const conversation = (name: string) => sessions.find((session) => heinzelOf(session.context) === name)?.id;
  const heinzel: SwitcherItem[] = heinzelmaennchen.map((found) => ({
    kind: "heinzel",
    id: qualified(found),
    title: found.title || found.name,
    state: found.state,
    session: conversation(qualified(found)),
  }));
  const ask: SwitcherItem = { kind: "ask", id: "ask", title: t("chat.ask") };
  const earlier: SwitcherItem[] = sessions
    .filter((session) => !mine.has(heinzelOf(session.context) ?? "") && (session.messages > 0 || session.pending > 0))
    .map((session) => ({
      kind: "session",
      id: session.id,
      title: session.title || t("chat.untitled"),
      updated: session.updated,
      spent: session.spent_usd,
      pending: session.pending,
      messages: session.messages,
    }));
  return [
    { label: t("chat.heinzelmaennchen"), items: heinzel.filter((item) => match(`${item.title} ${item.id}`)) },
    { label: t("chat.askGroup"), items: [ask].filter((item) => match(item.title)) },
    { label: t("chat.earlier"), items: earlier.filter((item) => match(item.title)) },
  ].filter((group) => group.items.length > 0);
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

/** "5 minutes ago", "yesterday": when a session last changed. */
export function ago(iso: string, now = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  for (const [unit, size] of steps) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return relative.format(0, "minute");
}

const stateLabel: Record<SchemaHeinzelSummary["state"], MessageKey> = {
  scheduled: "heinzel.state.scheduled",
  manual: "heinzel.state.manual",
  blocked: "heinzel.state.blocked",
  broken: "heinzel.state.broken",
};

export function SessionSwitcher({ chat, onNew }: { chat: Chat; onNew: () => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const sessions = useChatSessions(open);
  const heinzelmaennchen = useHeinzelmaennchen();
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const listId = useId();
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  const listed = heinzelmaennchen.data?.heinzelmaennchen;
  const all = useMemo(() => listed ?? [], [listed]);
  const groups = useMemo(() => switcherGroups(sessions.data ?? [], all, query.trim()), [sessions.data, all, query]);
  // A Heinzelmännchen's conversation is named after it; any other chat by its first message, until then it is new.
  const owner = heinzelOf(chat.session?.context);
  const named = all.find((found) => qualified(found) === owner);
  const current = named
    ? named.title || named.name
    : owner
      ? owner
      : chat.session?.messages?.length
        ? chat.session.title || t("chat.untitled")
        : t("chat.ask");
  const choose = (item: SwitcherItem) => {
    setOpen(false);
    setQuery("");
    trigger.current?.focus();
    if (item.kind === "ask") void chat.openGeneral();
    else if (item.kind === "heinzel") {
      if (item.id === owner) return; // already its conversation
      if (item.session) chat.select(item.session);
      else void chat.openAbout(`contract:${item.id}`);
    } else chat.select(item.id);
  };
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: Escape anywhere inside closes the list, as menus do
    <div
      ref={ref}
      className="relative min-w-0 flex-1"
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t("chat.switch", { title: current })}
        className="flex max-w-full items-center gap-1 rounded-lg px-1.5 py-1 text-ui font-medium transition-colors hover:bg-accent/70 "
      >
        <span className="truncate">{current}</span>
        <ChevronDown aria-hidden className="size-4 shrink-0 text-icon" />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={t("chat.sessions")}
          className="absolute top-full left-0 z-30 mt-1 w-[min(22rem,calc(100vw-2rem))] rounded-xl border border-border bg-popover p-1.5 shadow-xl"
        >
          <label className="flex items-center gap-2 rounded-lg bg-input px-2.5 py-1.5 text-icon focus-within:ring-1 focus-within:ring-ring">
            <Search aria-hidden className="size-4 shrink-0" />
            <span className="sr-only">{t("chat.searchSessions")}</span>
            <input
              type="search"
              // biome-ignore lint/a11y/noAutofocus: the list opened to be searched
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("chat.searchSessions")}
              aria-controls={listId}
              className="w-full bg-transparent text-ui text-foreground outline-none placeholder:text-muted-foreground"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onNew();
            }}
            className="mt-1 flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-ui hover:bg-accent [&_svg]:size-4 [&_svg]:text-icon"
          >
            <Plus aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block truncate">{t("chat.new")}</span>
              <span className="block truncate text-ui-sm text-muted-foreground">{t("chat.newHelp")}</span>
            </span>
          </button>
          <div id={listId} className="mt-1 max-h-80 overflow-y-auto">
            {(sessions.isLoading || heinzelmaennchen.isLoading) && (
              <p className="px-2.5 py-1.5 text-ui-sm text-muted-foreground">{t("state.loading")}</p>
            )}
            {groups.map((group) => (
              <section key={group.label} aria-label={group.label}>
                <h3 className="px-2.5 pt-2 pb-1 text-ui-sm text-muted-foreground">{group.label}</h3>
                <ul>
                  {group.items.map((item) => (
                    <li key={`${item.kind}:${item.id}`}>
                      <Row
                        item={item}
                        current={
                          (item.kind === "session" && item.id === chat.session?.id) ||
                          (item.kind === "heinzel" && item.id === owner)
                        }
                        onChoose={choose}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({
  item,
  current,
  onChoose,
}: {
  item: SwitcherItem;
  current: boolean;
  onChoose: (item: SwitcherItem) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onChoose(item)}
      aria-current={current || undefined}
      className={cn(
        "flex w-full items-start gap-2.5 rounded-lg px-2.5 py-1.5 text-left hover:bg-accent",
        current && "bg-accent text-accent-foreground",
      )}
    >
      {item.kind === "ask" ? (
        <MessageCircleQuestion aria-hidden className="mt-0.5 size-4 shrink-0 text-icon" />
      ) : (
        <span aria-hidden className="size-4 shrink-0" />
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-ui">{item.title}</span>
        <span className="block truncate text-ui-sm text-muted-foreground">
          {item.kind === "session"
            ? [
                ago(item.updated),
                t("chat.messages", { count: item.messages }),
                format.usd(item.spent),
                item.pending ? t("chat.pendingCount", { count: item.pending }) : "",
              ]
                .filter(Boolean)
                .join(" · ")
            : item.kind === "heinzel"
              ? [item.id, t(stateLabel[item.state]), item.session ? "" : t("chat.notStarted")]
                  .filter(Boolean)
                  .join(" · ")
              : t("chat.askHelp")}
        </span>
      </span>
      {current && <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-accent-foreground" />}
    </button>
  );
}
