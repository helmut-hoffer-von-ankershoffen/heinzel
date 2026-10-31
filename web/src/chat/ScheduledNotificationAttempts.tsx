import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, problem } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";

async function result<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok && data !== undefined) return data;
  throw problem(response.status, error);
}

const MAX_REVIEW_PAGES = 20;

type ScheduledAttempt = components["schemas"]["ScheduledNotificationView"];

function checked(value: components["schemas"]["ScheduledNotificationPage"], sessionId: string, cursor: number) {
  if (
    value.session_id !== sessionId ||
    value.items.some(
      (item, index) =>
        item.proposal.session_id !== sessionId ||
        !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(item.proposal.request_id) ||
        !Number.isInteger(item.seq) ||
        item.seq <= (index === 0 ? cursor : (value.items[index - 1]?.seq ?? cursor)) ||
        !["recorded", "unresolved", "abandoned"].includes(item.status),
    ) ||
    new Set(value.items.map((item) => item.proposal.request_id)).size !== value.items.length ||
    (value.next_cursor != null && value.next_cursor !== value.items.at(-1)?.seq)
  )
    throw new Error("Unexpected notification history");
}

/** Owner-private recorded attempts; recovery never sends or undoes a notice. */
export function ScheduledNotificationAttempts({ sessionId }: { sessionId: string }) {
  const client = useQueryClient();
  const [cursor, setCursor] = useState(0);
  const [ack, setAck] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState(false);
  const page = useQuery({
    queryKey: ["heinzel", "scheduled-notification-attempts", sessionId, cursor],
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/scheduled-notification-attempts", {
          params: { query: { session_id: sessionId, cursor, limit: 20 } },
        }),
      );
      checked(value, sessionId, cursor);
      return value;
    },
    retry: false,
  });
  // A stalled agent's cause can sit on any page of its oldest-first history, so look through all of them.
  const review = useQuery({
    queryKey: ["heinzel", "scheduled-notification-attempts", sessionId, "needs-review"],
    queryFn: async () => {
      const items: ScheduledAttempt[] = [];
      let after = 0;
      for (let pages = 0; pages < MAX_REVIEW_PAGES; pages++) {
        const value = await result(
          api.GET("/heinzel/scheduled-notification-attempts", {
            params: { query: { session_id: sessionId, cursor: after, limit: 100 } },
          }),
        );
        checked(value, sessionId, after);
        items.push(...value.items.filter((item) => item.status === "unresolved"));
        if (value.next_cursor == null) return { items, complete: true };
        after = value.next_cursor;
      }
      return { items, complete: false };
    },
    retry: false,
  });
  const needsReview = review.data?.items ?? [];
  const history = (page.data?.items ?? []).filter((item) => item.status !== "unresolved");
  const resolve = async (requestId: string) => {
    if (inFlight.current || busy || ack !== requestId) return;
    inFlight.current = true;
    setBusy(true);
    setError(false);
    try {
      const value = await result(
        api.POST("/heinzel/resolve-scheduled-notification", {
          body: { session_id: sessionId, request_id: requestId, acknowledge_uncertain: true },
        }),
      );
      if (
        value.proposal.session_id !== sessionId ||
        value.proposal.request_id !== requestId ||
        !["recorded", "abandoned"].includes(value.status)
      )
        throw new Error("Unexpected notification outcome");
      setAck(null);
    } catch {
      setError(true);
      setAck(null);
    } finally {
      setCursor(0);
      try {
        await client.invalidateQueries({ queryKey: ["heinzel", "scheduled-notification-attempts", sessionId] });
      } finally {
        inFlight.current = false;
        setBusy(false);
      }
    }
  };
  return (
    <section aria-label="Scheduled notification activity" className="space-y-2 rounded border border-border p-2">
      <h3 className="font-medium">Scheduled notification activity</h3>
      <p>Queued means recorded under local policy, not confirmed shown. Topics are model-chosen.</p>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => {
          setAck(null);
          if (cursor === 0) void page.refetch();
          else setCursor(0);
        }}
      >
        Refresh activity
      </Button>
      {page.isPending && <p>Loading notification activity…</p>}
      {(page.isError || review.isError) && <p role="alert">Notification activity could not be verified.</p>}
      {(needsReview.length > 0 || review.data?.complete === false) && (
        <p role="alert">
          This agent's scheduled turns are blocked until {needsReview.length === 1 ? "this outcome is" : "these outcomes are"}{" "}
          reviewed.
          {review.data?.complete === false && " More outcomes may need review in later attempts."}
        </p>
      )}
      {error && (
        <p role="alert">The outcome could not be verified. Refresh before choosing again; nothing is resent.</p>
      )}
      {page.data?.items.length === 0 && needsReview.length === 0 && <p>No scheduled notification attempts in this page.</p>}
      {[...needsReview, ...history].map(({ proposal, seq, status }) => (
        <section key={proposal.request_id} className="space-y-1 rounded border border-border p-2">
          <p className="break-words font-medium">{proposal.title}</p>
          <p className="whitespace-pre-wrap break-words">{proposal.body}</p>
          <p className="break-words">
            Topic: {proposal.topic} · {status === "recorded" ? "queued; not confirmed shown" : status}
          </p>
          {status === "unresolved" && (
            <div className="space-y-1">
              <p>
                Future scheduled turns are blocked until this outcome is reviewed. The notice may already have been
                queued and may still be delivered. Abandoning a chat turn does not resolve this notification. Recovery
                will inspect its receipt or set aside uncertainty; it will not resend or undo it.
              </p>
              <label className="block">
                <input
                  type="checkbox"
                  checked={ack === proposal.request_id}
                  disabled={busy}
                  onChange={(event) => setAck(event.target.checked ? proposal.request_id : null)}
                />{" "}
                I understand this notice may already have been queued.
              </label>
              <Button
                type="button"
                size="sm"
                disabled={busy || ack !== proposal.request_id}
                onClick={() => void resolve(proposal.request_id)}
              >
                Inspect or set aside outcome
              </Button>
            </div>
          )}
          <span className="sr-only">Attempt {seq}</span>
        </section>
      ))}
      <div className="flex flex-wrap gap-2">
        {cursor > 0 && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => {
              setAck(null);
              setCursor(0);
            }}
          >
            First attempts
          </Button>
        )}
        {page.data?.next_cursor != null && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => {
              setAck(null);
              setCursor(page.data?.next_cursor ?? 0);
            }}
          >
            More attempts
          </Button>
        )}
      </div>
    </section>
  );
}
