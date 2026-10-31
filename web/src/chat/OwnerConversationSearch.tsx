import { useEffect, useRef, useState } from "react";
import { api, problem } from "@/api/client";
import { Button } from "@/components/ui/button";

type Request = { query: string; since: string | null; until: string | null };
type Hit = { session: string; seq: number; text: string; at: string; cursor: string };
const BATCH = 20;

/** Owner-wide search uses bounded server steps; cursors and results never grant access. */
export function OwnerConversationSearch({ onJump }: { onJump: (session: string, cursor: string) => void }) {
  const [query, setQuery] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [request, setRequest] = useState<Request | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [status, setStatus] = useState("idle");
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      generation.current++;
      controller.current?.abort();
    },
    [],
  );

  function cancel() {
    generation.current++;
    controller.current?.abort();
    controller.current = null;
  }
  function reset() {
    cancel();
    setHits([]);
    setRequest(null);
    setNext(null);
    setStatus("idle");
  }
  async function run(terms: Request, cursor: string | null) {
    cancel();
    const runId = generation.current;
    const abort = new AbortController();
    controller.current = abort;
    setStatus("searching");
    setHits([]);
    let position = cursor;
    try {
      for (let step = 0; step < BATCH; step++) {
        const { data, error, response } = await api.POST("/heinzel/search-agent-conversations", {
          body: { request: { ...terms, cursor: position } },
          signal: abort.signal,
        });
        if (generation.current !== runId) return;
        if (!response.ok || !data) throw problem(response.status, error);
        if (
          data.completeness !== "unknown" ||
          data.items.length > 50 ||
          data.items.length !== (data.jump_cursors ?? []).length ||
          (data.items.length && !data.session_id)
        )
          throw new Error("Invalid search page");
        const session = data.session_id;
        const found = data.items.map((item, index) => {
          const jump = data.jump_cursors?.[index];
          if (!session || !jump) throw new Error("Invalid message cursor");
          return { session, seq: item.seq, text: item.text, at: item.at, cursor: jump };
        });
        if (found.length) setHits(found);
        position = data.next_cursor;
        setNext(position);
        if (data.status === "blocked") {
          setStatus("blocked");
          return;
        }
        if (!position) {
          setStatus("done");
          return;
        }
        if (found.length) {
          setStatus("paused");
          return;
        }
      }
      setStatus("paused");
    } catch {
      if (generation.current === runId) setStatus("error");
    } finally {
      if (generation.current === runId) controller.current = null;
    }
  }
  function start() {
    try {
      const terms = {
        query,
        since: since ? new Date(since).toISOString() : null,
        until: until ? new Date(until).toISOString() : null,
      };
      if (!terms.query && !terms.since && !terms.until) throw new Error("Empty search");
      reset();
      setRequest(terms);
      void run(terms, null);
    } catch {
      setStatus("error");
    }
  }
  return (
    <details className="rounded-lg border border-border p-3">
      <summary className="cursor-pointer text-sm font-medium">Search all Heinzel conversations</summary>
      <section aria-label="Search all conversations" className="mt-3 min-w-0 space-y-3 text-sm">
        <label className="block">
          Text
          <input
            className="block w-full rounded border bg-background p-2"
            value={query}
            maxLength={200}
            onChange={(event) => {
              reset();
              setQuery(event.target.value);
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
              onChange={(event) => {
                reset();
                setSince(event.target.value);
              }}
            />
          </label>
          <label>
            To
            <input
              className="block w-full min-w-0 rounded border bg-background p-2"
              type="datetime-local"
              value={until}
              onChange={(event) => {
                reset();
                setUntil(event.target.value);
              }}
            />
          </label>
        </div>
        <p>
          Times use your device's timezone. Results may be incomplete if message bodies expired or an index needs
          rebuilding. Continuing replaces the current results with the next page.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" onClick={start}>
            Search
          </Button>
          {status === "searching" && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                cancel();
                setStatus("paused");
              }}
            >
              Stop
            </Button>
          )}
          {(status === "paused" || status === "blocked") && next && request && (
            <Button type="button" size="sm" variant="outline" onClick={() => void run(request, next)}>
              Continue search
            </Button>
          )}
        </div>
        {status === "searching" && <p role="status">Searching and preparing conversation indexes…</p>}
        {status === "paused" && <p role="status">Search paused. Continue to scan more conversations.</p>}
        {status === "blocked" && <p role="alert">A conversation could not be searched. Retry after it is repaired.</p>}
        {status === "error" && <p role="alert">Search could not be completed. Check the time range and try again.</p>}
        {status === "done" && <p role="status">Search finished. Results may still be incomplete.</p>}
        <ol className="space-y-2">
          {hits.map((hit) => (
            <li key={`${hit.session}:${hit.seq}`} className="min-w-0 rounded border border-border p-2">
              <p>Heinzel {hit.session}</p>
              <time dateTime={hit.at}>{new Date(hit.at).toLocaleString()}</time>
              <p className="whitespace-pre-wrap break-words">{hit.text}</p>
              <Button type="button" variant="outline" size="sm" onClick={() => onJump(hit.session, hit.cursor)}>
                Open message
              </Button>
            </li>
          ))}
        </ol>
      </section>
    </details>
  );
}
