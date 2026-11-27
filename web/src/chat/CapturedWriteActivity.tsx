import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { api, type SchemaChatSession, unwrap } from "@/api/client";
import { token } from "@/api/token";
import { Button } from "@/components/ui/button";

const statusLabel = {
  draft: "Awaiting review",
  discarded: "Set aside",
  uncertain: "Write outcome unknown",
  acknowledged: "Provider acknowledged the write",
};

/** Owner-private history of recorded writes. Reading this panel never authorizes a write. */
export function CapturedWriteActivity({
  sessionId,
  conversationBusy = false,
  onReview,
}: {
  sessionId: string;
  conversationBusy?: boolean;
  onReview: (chat: SchemaChatSession) => void;
}) {
  const viewId = useId();
  const [open, setOpen] = useState(false);
  const [before, setBefore] = useState<number>();
  const mutation = useRef<AbortController | null>(null);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string>();
  useEffect(() => () => mutation.current?.abort(), []);
  const query = useQuery({
    queryKey: ["heinzel", "captured-write-activity", viewId, sessionId, before],
    enabled: open,
    gcTime: 0,
    retry: false,
    queryFn: async ({ signal }) => {
      const authority = token();
      const page = await unwrap(
        api.GET("/heinzel/captured-write-activity-page", {
          params: { query: { session_id: sessionId, before } },
          signal,
        }),
      );
      if (page.session_id !== sessionId || token() !== authority) throw new Error("Write activity scope changed");
      return { page, isCurrent: () => token() === authority };
    },
  });
  const page = !query.isError && query.data?.isCurrent() ? query.data.page : undefined;
  const busy = working || conversationBusy;
  async function stage(requestId: string) {
    if (conversationBusy || mutation.current) return;
    const authority = token();
    const controller = new AbortController();
    mutation.current = controller;
    setWorking(true);
    setMessage(undefined);
    try {
      const chat = await unwrap(
        api.POST("/system/runner/chat/propose-captured-write", {
          params: { query: { durable_session: sessionId, request_id: requestId } },
          signal: controller.signal,
        }),
      );
      const actions = chat.actions?.filter(
        (action) =>
          action.module === "heinzel" &&
          action.operation === "execute-captured-write" &&
          action.arguments?.session_id === sessionId &&
          action.arguments?.request_id === requestId,
      );
      if (controller.signal.aborted || token() !== authority) return;
      const action = actions?.[0];
      if (
        chat.personal_session_id !== sessionId ||
        actions?.length !== 1 ||
        action?.status !== "pending" ||
        !action.arguments?.review ||
        typeof action.arguments.review !== "object"
      ) {
        setMessage("The exact write review is unavailable. Refresh the activity to inspect its outcome.");
        return;
      }
      onReview(chat);
    } catch {
      if (!controller.signal.aborted) setMessage("Couldn’t open the write review.");
    } finally {
      mutation.current = null;
      if (!controller.signal.aborted) setWorking(false);
    }
  }

  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Document write activity</summary>
      {open && (
        <div className="space-y-2 text-sm">
          {message && <p role="alert">{message}</p>}
          {query.isFetching && <p role="status">Checking write activity…</p>}
          {query.isError && <p role="alert">Couldn’t check write activity.</p>}
          {page?.items.length === 0 && <p>No recorded document writes.</p>}
          {page && page.items.length > 0 && (
            <ul className="space-y-2">
              {page.items.map((item) => (
                <li key={item.request_id} className="break-words">
                  <strong>{statusLabel[item.status]}</strong>
                  <span className="block">{item.reference}</span>
                  {item.status === "draft" && (
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => void stage(item.request_id)}>
                      Review write
                    </Button>
                  )}
                  {item.revision && <span className="block">Recorded revision: {item.revision}</span>}
                </li>
              ))}
            </ul>
          )}
          <div className="flex gap-2">
            {before !== undefined && (
              <Button size="sm" variant="outline" onClick={() => setBefore(undefined)}>
                Newest
              </Button>
            )}
            {page?.next_before != null && (
              <Button size="sm" variant="outline" onClick={() => setBefore(page.next_before ?? undefined)}>
                Older
              </Button>
            )}
            <Button size="sm" variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>
              Refresh
            </Button>
          </div>
        </div>
      )}
    </details>
  );
}
