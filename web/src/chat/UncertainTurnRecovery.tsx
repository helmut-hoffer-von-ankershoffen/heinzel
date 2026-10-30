import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, problem } from "@/api/client";
import { Button } from "@/components/ui/button";

/** An explicit owner decision; neither a retry nor an assertion that effects were undone. */
export function UncertainTurnRecovery({
  sessionId,
  requestId,
  onResolved,
}: {
  sessionId: string;
  requestId: string;
  onResolved: () => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const state = useQuery({
    queryKey: ["heinzel", "chat-turn-state", sessionId, requestId],
    queryFn: async () => {
      const {
        data,
        error: failure,
        response,
      } = await api.GET("/heinzel/chat-turn-state", {
        params: { query: { session_id: sessionId, request_id: requestId } },
      });
      if (!response.ok || !data) throw problem(response.status, failure);
      if (data.session_id !== sessionId || data.request_id !== requestId) throw new Error("Unexpected turn");
      return data;
    },
    retry: false,
    staleTime: 0,
    refetchOnMount: "always",
  });
  const setAside = async () => {
    if (!acknowledged || busy || state.isError || state.isFetching || state.data?.status !== "unresolved") return;
    setBusy(true);
    setError(false);
    try {
      const {
        data,
        error: failure,
        response,
      } = await api.POST("/heinzel/abandon-chat-turn", {
        body: { session_id: sessionId, request_id: requestId, acknowledge_uncertain: true },
      });
      if (!response.ok || !data) throw problem(response.status, failure);
      if (data.session_id !== sessionId || data.request_id !== requestId || data.status !== "abandoned")
        throw new Error("Unexpected resolution");
    } catch {
      setError(true);
    } finally {
      // The durable read is authoritative even when the acknowledgement was lost.
      await state.refetch();
      setBusy(false);
    }
  };
  return (
    <section aria-label="Resolve an uncertain turn" className="space-y-2 rounded border border-border p-2 text-sm">
      {state.isError && <p role="alert">The saved turn state could not be checked.</p>}
      {error && <p role="alert">The set-aside request was not confirmed. Check the saved state before proceeding.</p>}
      {state.data?.status === "unresolved" && (
        <>
          <p>This turn has no recorded answer and may still be running. Its possible effects remain unknown.</p>
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
            <span>I understand setting this turn aside will not retry it or undo any possible effects.</span>
          </label>
          <Button
            type="button"
            size="sm"
            disabled={!acknowledged || busy || state.isError || state.isFetching}
            onClick={() => void setAside()}
          >
            Set aside this turn
          </Button>
        </>
      )}
      {state.data?.status === "abandoned" && (
        <p role="status">This turn was set aside. Its possible effects remain unknown.</p>
      )}
      {state.data?.status === "completed" && <p role="status">An answer was recorded for this turn.</p>}
      {(state.data?.status === "abandoned" || state.data?.status === "completed") && (
        <Button type="button" size="sm" disabled={busy || state.isFetching || state.isError} onClick={onResolved}>
          Continue conversation
        </Button>
      )}
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={busy || state.isFetching}
        onClick={() => void state.refetch()}
      >
        Check saved turn
      </Button>
    </section>
  );
}
