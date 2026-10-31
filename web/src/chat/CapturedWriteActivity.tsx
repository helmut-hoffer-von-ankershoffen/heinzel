import { useEffect, useState } from "react";
import { api, problem, type SchemaCapturedWriteActivityPage } from "@/api/client";
import { Button } from "@/components/ui/button";

/** Owner-private host evidence; this view never contacts a provider or authorizes a write. */
export function CapturedWriteActivity({ sessionId }: { sessionId: string }) {
  const [before, setBefore] = useState<number | null>(null);
  const [version, setVersion] = useState(0);
  const [loaded, setLoaded] = useState<{
    before: number | null;
    version: number;
    data: SchemaCapturedWriteActivityPage;
  } | null>(null);
  const page =
    loaded?.before === before && loaded.version === version && loaded.data.session_id === sessionId
      ? loaded.data
      : null;
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    const abort = new AbortController();
    setLoaded(null);
    setFailed(false);
    void (async () => {
      const { data, error, response } = await api.GET("/heinzel/captured-write-activity-page", {
        params: { query: { session_id: sessionId, before: before ?? undefined, limit: 20 } },
        signal: abort.signal,
      });
      if (!response.ok || !data) throw problem(response.status, error);
      if (
        data.session_id !== sessionId ||
        data.items.length > 20 ||
        data.items.some(
          (item, index) =>
            !item.request_id ||
            !item.reference ||
            !item.provider ||
            !item.account ||
            !item.source ||
            !Number.isSafeInteger(item.seq) ||
            item.seq < 1 ||
            (index === 0 ? before !== null && item.seq >= before : item.seq >= (data.items[index - 1]?.seq ?? 0)) ||
            !["draft", "uncertain", "acknowledged"].includes(item.status) ||
            (item.status !== "acknowledged" && item.revision != null) ||
            Number.isNaN(Date.parse(item.at)),
        ) ||
        new Set(data.items.map((item) => item.request_id)).size !== data.items.length ||
        (data.next_before != null && data.next_before !== data.items.at(-1)?.seq)
      )
        throw new Error("Invalid write activity");
      if (active) setLoaded({ before, version, data });
    })().catch(() => {
      if (active) setFailed(true);
    });
    return () => {
      active = false;
      abort.abort();
    };
  }, [sessionId, before, version]);
  return (
    <section
      aria-label="Recorded artifact write activity"
      className="space-y-2 rounded border border-border p-2 text-sm"
    >
      <h3 className="font-medium">Recorded write activity</h3>
      <p>
        A provider acknowledgement is a recorded response, not a later verification of the artifact. Reading or linking
        a file does not establish an edit.
      </p>
      {page === null && !failed && <p role="status">Loading write activity…</p>}
      {failed && <p role="alert">Write activity could not be verified.</p>}
      {page?.items.length === 0 && <p>No recorded write activity on this page.</p>}
      {page?.items.map((item) => (
        <section key={item.request_id} className="space-y-1 rounded border border-border p-2">
          <p className="font-medium">
            {item.status === "draft"
              ? "Draft; no confirmed attempt"
              : item.status === "uncertain"
                ? "Confirmed attempt; outcome unknown"
                : "Provider acknowledged the attempt"}
          </p>
          <p className="break-all">{item.reference}</p>
          <p className="break-words">
            Provider: {item.provider} · Account: {item.account} · Source: {item.source}
          </p>
          <time dateTime={item.at}>{new Date(item.at).toLocaleString()}</time>
          {item.status === "acknowledged" && item.revision && (
            <p className="break-all">Provider-reported revision: {item.revision}</p>
          )}
          {item.status === "uncertain" && (
            <p>The write may have happened; its outcome must be inspected before another attempt.</p>
          )}
        </section>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => setVersion((current) => current + 1)}>
          Refresh activity
        </Button>
        {before !== null && (
          <Button type="button" size="sm" variant="outline" onClick={() => setBefore(null)}>
            Latest activity
          </Button>
        )}
        {page?.next_before != null && (
          <Button type="button" size="sm" variant="outline" onClick={() => setBefore(page?.next_before ?? null)}>
            Older activity
          </Button>
        )}
      </div>
    </section>
  );
}
