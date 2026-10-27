// Which conversation the chat shows: its title with a chevron that opens a searchable list of the sessions hz keeps
// (`hz chat list`), a general "Workshop" chat and "New chat". The list is grouped, so the durable Heinzelmännchen of
// ADR 0049 (`hz heinzel list`) become one more group of items with their own fields, not a new component.

import { Check, ChevronDown, Hammer, Plus } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useChatSessions } from "@/api/chat";
import type { SchemaChatSummary } from "@/api/client";
import { SearchField } from "@/components/ui/search-field";
import { format, t } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Chat } from "./useChat";

/** One row of the switcher. `heinzel` is reserved for the durable Heinzelmännchen, listed once the API offers them. */
export type SwitcherItem =
  | { kind: "workshop"; id: "workshop"; title: string }
  | { kind: "session"; id: string; title: string; updated: string; spent: number; pending: number; messages: number }
  | { kind: "heinzel"; id: string; title: string; artifact?: string; state?: string; unread?: number };

export type SwitcherGroup = { label: string; items: SwitcherItem[] };

/** The groups in the order shown: the general chat, then the recent sessions; empty groups go. */
export function switcherGroups(sessions: SchemaChatSummary[], needle: string): SwitcherGroup[] {
  const match = (text: string) => !needle || text.toLowerCase().includes(needle.toLowerCase());
  const workshop: SwitcherItem = { kind: "workshop", id: "workshop", title: t("chat.workshop") };
  const recent: SwitcherItem[] = sessions.map((session) => ({
    kind: "session",
    id: session.id,
    title: session.title || t("chat.untitled"),
    updated: session.updated,
    spent: session.spent_usd,
    pending: session.pending,
    messages: session.messages,
  }));
  return [
    { label: t("chat.start"), items: [workshop].filter((item) => match(item.title)) },
    { label: t("chat.recent"), items: recent.filter((item) => match(item.title)) },
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

export function SessionSwitcher({ chat, onNew }: { chat: Chat; onNew: () => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const sessions = useChatSessions(open);
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
  const groups = useMemo(() => switcherGroups(sessions.data ?? [], query.trim()), [sessions.data, query]);
  // A chat is named by its first message; until then it is a new chat, whatever it is about.
  const current = chat.session?.messages?.length ? chat.session.title || t("chat.untitled") : t("chat.new");
  const choose = (item: SwitcherItem) => {
    setOpen(false);
    setQuery("");
    trigger.current?.focus();
    if (item.kind === "workshop") void chat.openGeneral();
    else if (item.kind === "session") chat.select(item.id);
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
        className="flex max-w-full items-center gap-1 py-1 pr-2.5 pl-1.5 text-ui font-medium transition-colors hover:bg-accent/70 "
      >
        <span className="truncate">{current}</span>
        <ChevronDown aria-hidden className="size-4 shrink-0 text-icon" />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={t("chat.sessions")}
          className="absolute top-full left-0 z-(--layer-popover) mt-1 w-[min(22rem,calc(100vw-2rem))] rounded-xl border border-border bg-popover p-1.5 shadow-xl"
        >
          <SearchField
            quietFocus
            label={t("chat.searchSessions")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("chat.searchSessions")}
            aria-controls={listId}
          />
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onNew();
            }}
            className="mt-1 flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-ui hover:bg-accent [&_svg]:size-4 [&_svg]:text-icon"
          >
            <Plus aria-hidden />
            {t("chat.new")}
          </button>
          <div id={listId} className="mt-1 max-h-80 overflow-y-auto">
            {sessions.isLoading && (
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
                        current={item.kind === "session" && item.id === chat.session?.id}
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
      {item.kind === "workshop" ? (
        <Hammer aria-hidden className="mt-0.5 size-4 shrink-0 text-icon" />
      ) : (
        <span aria-hidden className="size-4 shrink-0" />
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-ui">{item.title}</span>
        {item.kind === "session" && (
          <span className="block truncate text-ui-sm text-muted-foreground">
            {[
              ago(item.updated),
              t("chat.messages", { count: item.messages }),
              format.usd(item.spent),
              item.pending ? t("chat.pendingCount", { count: item.pending }) : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        )}
        {item.kind === "workshop" && (
          <span className="block truncate text-ui-sm text-muted-foreground">{t("chat.workshopHelp")}</span>
        )}
      </span>
      {current && <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-accent-foreground" />}
    </button>
  );
}
