import { useRef, useState } from "react";
import { api, problem } from "@/api/client";
import { Button } from "@/components/ui/button";

type SearchRequest = { session_id: string; query: string; since: string | null; until: string | null };
type Hit = { seq: number; text: string; at: string; cursor: string };

/** Search one owner-private conversation; the derived index never claims complete history. */
export function ConversationSearch({ sessionId, onJump }: { sessionId: string; onJump: (cursor: string) => void }) {
  const [query, setQuery] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [submitted, setSubmitted] = useState<SearchRequest | null>(null);
  const [hits, setHits] = useState<Hit[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [mode, setMode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const active = useRef(false);

  async function run(request: SearchRequest, cursor: string | null) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setError(false);
    try {
      const {
        data,
        error: failure,
        response,
      } = await api.POST("/heinzel/indexed-conversation-search", {
        body: { request: { ...request, cursor } },
      });
      if (!response.ok || !data) throw problem(response.status, failure);
      if (
        data.session_id !== sessionId ||
        data.items.length > 50 ||
        data.items.length !== (data.jump_cursors ?? []).length ||
        data.completeness !== "unknown" ||
        !["trigram", "bounded-scan"].includes(data.mode)
      )
        throw new Error("Invalid search page");
      const page = data.items.map((item, index) => {
        const jump = data.jump_cursors?.[index];
        if (typeof jump !== "string" || !jump) throw new Error("Invalid message cursor");
        return { seq: item.seq, text: item.text, at: item.at, cursor: jump };
      });
      setHits(page);
      setSubmitted(request);
      setNext(data.next_cursor);
      setMode(data.mode);
    } catch {
      setError(true);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }

  function clearResults() {
    setHits([]);
    setSubmitted(null);
    setNext(null);
    setMode(null);
    setError(false);
  }

  function start() {
    if (active.current) return;
    try {
      const request: SearchRequest = {
        session_id: sessionId,
        query,
        since: since ? new Date(since).toISOString() : null,
        until: until ? new Date(until).toISOString() : null,
      };
      if (!request.query && !request.since && !request.until) throw new Error("Empty search");
      clearResults();
      void run(request, null);
    } catch {
      setError(true);
    }
  }

  return (
    <details className="rounded-lg border border-border p-3">
      <summary className="cursor-pointer text-sm font-medium">Search conversation</summary>
      <section aria-label="Search conversation" className="mt-3 min-w-0 space-y-3 text-sm">
        <label className="block">
          Text
          <input
            className="block w-full rounded border bg-background p-2"
            value={query}
            maxLength={200}
            disabled={busy}
            onChange={(event) => {
              setQuery(event.target.value);
              clearResults();
            }}
          />
        </label>
        <div className="grid gap-2 sm:grid-cols-2">
          <label>
            From
            <input
              className="block w-full min-w-0 rounded border bg-background p-2"
              type="datetime-local"
              value={since}
              disabled={busy}
              onChange={(event) => {
                setSince(event.target.value);
                clearResults();
              }}
            />
          </label>
          <label>
            To
            <input
              className="block w-full min-w-0 rounded border bg-background p-2"
              type="datetime-local"
              value={until}
              disabled={busy}
              onChange={(event) => {
                setUntil(event.target.value);
                clearResults();
              }}
            />
          </label>
        </div>
        <p>Times use your device's timezone.</p>
        <Button type="button" size="sm" disabled={busy} onClick={start}>
          Search
        </Button>
        {error && (
          <p role="alert">Search could not be completed. Check the time range or browse the conversation pages.</p>
        )}
        {mode && (
          <p>
            Results may be incomplete if message bodies have expired or the search index needs rebuilding.{" "}
            {mode === "bounded-scan" && "Short or time-only searches scan one page at a time."}
          </p>
        )}
        {hits.length > 0 && (
          <ol className="space-y-2">
            {hits.map((hit) => (
              <li key={hit.seq} className="min-w-0 rounded border border-border p-2">
                <time dateTime={hit.at}>{new Date(hit.at).toLocaleString()}</time>
                <p className="whitespace-pre-wrap break-words">{hit.text}</p>
                <Button type="button" variant="outline" size="sm" onClick={() => onJump(hit.cursor)}>
                  Open message
                </Button>
              </li>
            ))}
          </ol>
        )}
        {submitted && hits.length === 0 && <p>No matches in the scanned portion.</p>}
        {next && submitted && (
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void run(submitted, next)}>
            Search more
          </Button>
        )}
      </section>
    </details>
  );
}
