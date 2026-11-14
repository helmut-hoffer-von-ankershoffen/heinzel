// One search field for the Heinzel chat (canvas board Search): this chat or all Heinzels, results as a list, and a
// result jumps to its message. The server pages are bounded and never claim complete history.
import { useEffect, useRef, useState } from "react";
import { api, problem } from "@/api/client";
import { useHeinzelSessions } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

type Scope = "chat" | "all";
type Hit = { session: string; seq: number; text: string; at: string; cursor: string };
type Page = { hits: Hit[]; next: string | null; blocked: boolean };
const STEPS = 20;
const MAX_ITEMS = 50;

function checked(items: unknown[], cursors: string[] | undefined) {
  if (items.length > MAX_ITEMS || items.length !== (cursors ?? []).length) throw new Error("Invalid search page");
}

async function searchChat(sessionId: string, query: string, cursor: string | null, signal: AbortSignal): Promise<Page> {
  const { data, error, response } = await api.POST("/heinzel/indexed-conversation-search", {
    body: { request: { session_id: sessionId, query, since: null, until: null, cursor } },
    signal,
  });
  if (!response.ok || !data) throw problem(response.status, error);
  checked(data.items, data.jump_cursors);
  if (data.session_id !== sessionId || data.completeness !== "unknown") throw new Error("Invalid search page");
  const hits = data.items.map((item, index) => ({
    session: sessionId,
    seq: item.seq,
    text: item.text,
    at: item.at,
    cursor: data.jump_cursors?.[index] ?? "",
  }));
  if (hits.some((hit) => !hit.cursor)) throw new Error("Invalid message cursor");
  return { hits, next: data.next_cursor, blocked: false };
}

/** The all-Heinzels endpoint answers one chat per step: go on until a step has results or the scan ends. */
async function searchAll(query: string, start: string | null, signal: AbortSignal): Promise<Page> {
  let position = start;
  for (let step = 0; step < STEPS; step++) {
    const { data, error, response } = await api.POST("/heinzel/search-agent-conversations", {
      body: { request: { query, since: null, until: null, cursor: position } },
      signal,
    });
    if (!response.ok || !data) throw problem(response.status, error);
    checked(data.items, data.jump_cursors);
    if (data.completeness !== "unknown" || (data.items.length && !data.session_id)) throw new Error("Invalid page");
    const session = data.session_id ?? "";
    const hits = data.items.map((item, index) => ({
      session,
      seq: item.seq,
      text: item.text,
      at: item.at,
      cursor: data.jump_cursors?.[index] ?? "",
    }));
    if (hits.some((hit) => !hit.cursor)) throw new Error("Invalid message cursor");
    position = data.next_cursor;
    if (data.status === "blocked") return { hits, next: position, blocked: true };
    if (!position || hits.length) return { hits, next: position, blocked: false };
  }
  return { hits: [], next: position, blocked: false };
}

export function ChatSearch({
  sessionId,
  onClose,
  onJump,
}: {
  sessionId: string;
  onClose: () => void;
  onJump: (session: string, cursor: string) => void;
}) {
  const [scope, setScope] = useState<Scope>("chat");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [asked, setAsked] = useState<{ scope: Scope; query: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const names = new Map((useHeinzelSessions().data?.sessions ?? []).map((item) => [item.session_id, item.name]));
  useEffect(() => {
    input.current?.focus();
    return () => controller.current?.abort();
  }, []);

  function reset() {
    generation.current++;
    controller.current?.abort();
    setHits([]);
    setNext(null);
    setAsked(null);
    setBusy(false);
    setFailed(false);
    setBlocked(false);
  }
  async function run(request: { scope: Scope; query: string }, cursor: string | null) {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const run = ++generation.current;
    setBusy(true);
    setFailed(false);
    try {
      const page =
        request.scope === "chat"
          ? await searchChat(sessionId, request.query, cursor, abort.signal)
          : await searchAll(request.query, cursor, abort.signal);
      if (generation.current !== run) return;
      setHits((current) => (cursor ? [...current, ...page.hits] : page.hits));
      setNext(page.next);
      setBlocked(page.blocked);
      setAsked(request);
    } catch {
      if (generation.current === run) setFailed(true);
    } finally {
      if (generation.current === run) setBusy(false);
    }
  }
  function submit() {
    const text = query.trim();
    if (!text || busy) return;
    reset();
    void run({ scope, query: text }, null);
  }

  const count = hits.length === 1 ? t("chatSearch.count.one") : t("chatSearch.count.many", { count: hits.length });
  const radio = (value: Scope, label: string) => (
    <label
      className={cn(
        "flex min-h-11 flex-1 cursor-pointer items-center justify-center rounded-lg border border-border px-3 text-sm",
        "has-[:checked]:bg-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
      )}
    >
      <input
        type="radio"
        name="search-scope"
        className="sr-only"
        checked={scope === value}
        onChange={() => {
          reset();
          setScope(value);
        }}
      />
      {label}
    </label>
  );
  return (
    // biome-ignore lint/a11y/useSemanticElements: a form with role="search" is the labelled landmark and submits on Enter
    <form
      role="search"
      aria-label={t("chatSearch.label")}
      className="min-w-0 space-y-3 rounded-lg border border-border p-3 text-sm"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="flex flex-wrap gap-2">
        <input
          ref={input}
          type="search"
          aria-label={t("chatSearch.field")}
          className="block min-h-11 w-full min-w-0 rounded-lg border bg-background px-3"
          value={query}
          maxLength={200}
          onChange={(event) => {
            reset();
            setQuery(event.target.value);
          }}
        />
        <Button type="submit" className="min-h-11" disabled={busy || !query.trim()}>
          {t("chatSearch.go")}
        </Button>
        <Button type="button" variant="outline" className="min-h-11" onClick={onClose}>
          {t("chatSearch.close")}
        </Button>
      </div>
      <fieldset className="flex gap-2">
        <legend className="sr-only">{t("chatSearch.scope")}</legend>
        {radio("chat", t("chatSearch.scopeChat"))}
        {radio("all", t("chatSearch.scopeAll"))}
      </fieldset>
      <p role="status">
        {busy ? t("chatSearch.searching") : asked && !failed ? (hits.length ? count : t("chatSearch.none")) : ""}
      </p>
      {failed && <p role="alert">{t("chatSearch.error")}</p>}
      {blocked && <p role="status">{t("chatSearch.partial")}</p>}
      {hits.length > 0 && (
        <>
          <ol className="space-y-2">
            {hits.map((hit) => {
              const time = new Date(hit.at).toLocaleString();
              return (
                <li key={`${hit.session}:${hit.seq}`}>
                  <button
                    type="button"
                    className="block min-h-11 w-full min-w-0 rounded-lg border border-border px-3 py-2 text-left hover:bg-accent"
                    onClick={() => onJump(hit.session, hit.cursor)}
                  >
                    <span className="block text-xs text-muted-foreground">
                      {scope === "all" ? `${names.get(hit.session) ?? t("chatSearch.scopeAll")} · ` : ""}
                      <time dateTime={hit.at}>{time}</time>
                    </span>
                    <span className="block whitespace-pre-wrap break-words">{hit.text}</span>
                  </button>
                </li>
              );
            })}
          </ol>
          <p className="text-xs text-muted-foreground">{t("chatSearch.incomplete")}</p>
        </>
      )}
      {next && asked && (
        <Button
          type="button"
          variant="outline"
          className="min-h-11"
          disabled={busy}
          onClick={() => void run(asked, next)}
        >
          {t("chatSearch.more")}
        </Button>
      )}
    </form>
  );
}
