import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api, authorityMiddleware, problem } from "@/api/client";
import { authorityEpoch, subscribeAuthority } from "@/api/token";
import { Button } from "@/components/ui/button";

/** The server's recorded state of an accepted turn; shared by the recovery card and the message states. */
export function useTurnState(sessionId: string, requestId: string, enabled = true) {
  const authority = useSyncExternalStore(subscribeAuthority, authorityEpoch);
  return useQuery({
    queryKey: turnStateKey(sessionId, requestId, authority),
    queryFn: ({ signal }) => fetchTurnState(sessionId, requestId, authority, signal),
    retry: false,
    staleTime: 0,
    refetchOnMount: "always",
    enabled,
    gcTime: 0,
  });
}

export const turnStateKey = (sessionId: string, requestId: string, authority = authorityEpoch()) =>
  ["heinzel", "chat-turn-state", sessionId, requestId, authority] as const;

/** What hz knows about one accepted turn: pending, unresolved (uncertain), abandoned or completed. */
export async function fetchTurnState(
  sessionId: string,
  requestId: string,
  authority = authorityEpoch(),
  signal?: AbortSignal,
) {
  const {
    data,
    error: failure,
    response,
  } = await api.GET("/heinzel/chat-turn-state", {
    params: { query: { session_id: sessionId, request_id: requestId } },
    middleware: authorityMiddleware(authority),
    signal,
  });
  signal?.throwIfAborted();
  if (authorityEpoch() !== authority) throw new Error("Browser authority changed");
  if (!response.ok || !data) throw problem(response.status, failure);
  if (data.session_id !== sessionId || data.request_id !== requestId) throw new Error("Unexpected turn");
  return data;
}

/** Bind each recovery action to its original mounted request and authority, including delayed continuations. */
export function useTurnAction(sessionId: string, requestId: string) {
  const authority = useSyncExternalStore(subscribeAuthority, authorityEpoch);
  const target = `${authority}:${sessionId}:${requestId}`;
  const latest = useRef(target);
  latest.current = target;
  const generation = useRef(0);
  const mounted = useRef<string | null>(null);
  const controllers = useRef(new Set<AbortController>());
  useEffect(() => {
    mounted.current = target;
    return () => {
      mounted.current = null;
      generation.current++;
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear();
    };
  }, [target]);
  return () => {
    const version = generation.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    return {
      authority,
      signal: controller.signal,
      current: () =>
        mounted.current === target &&
        latest.current === target &&
        generation.current === version &&
        authorityEpoch() === authority &&
        !controller.signal.aborted,
      finish: () => controllers.current.delete(controller),
    };
  };
}

/** An explicit owner decision; neither a retry nor an assertion that effects were undone. */
export function UncertainTurnRecovery({
  sessionId,
  requestId,
  onResolved,
  allowPendingDaemon = false,
  allowPendingExpired = false,
}: {
  sessionId: string;
  requestId: string;
  onResolved: () => void;
  allowPendingDaemon?: boolean;
  allowPendingExpired?: boolean;
}) {
  const [acknowledgement, setAcknowledgement] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const state = useTurnState(sessionId, requestId);
  const begin = useTurnAction(sessionId, requestId);
  const acknowledgementKey = `${authorityEpoch()}:${sessionId}:${requestId}:${state.data?.status}`;
  const acknowledged = acknowledgement === acknowledgementKey;
  const setAside = async () => {
    const status = state.data?.status;
    if (
      !acknowledged ||
      busy ||
      state.isError ||
      state.isFetching ||
      !(status === "unresolved" || ((allowPendingDaemon || allowPendingExpired) && status === "pending"))
    )
      return;
    setBusy(true);
    setError(false);
    const action = begin();
    try {
      if (!action.current()) return;
      const {
        data,
        error: failure,
        response,
      } = await api.POST("/heinzel/abandon-chat-turn", {
        middleware: authorityMiddleware(action.authority),
        signal: action.signal,
        body: {
          session_id: sessionId,
          request_id: requestId,
          acknowledge_uncertain: true,
          expected_status: status,
        },
      });
      if (!action.current()) return;
      if (!response.ok || !data) throw problem(response.status, failure);
      if (data.session_id !== sessionId || data.request_id !== requestId || data.status !== "abandoned")
        throw new Error("Unexpected resolution");
    } catch {
      if (action.current()) setError(true);
    } finally {
      // The durable read is authoritative even when the acknowledgement was lost.
      if (action.current()) await state.refetch();
      if (action.current()) setBusy(false);
      action.finish();
    }
  };
  return (
    <section aria-label="Resolve an uncertain turn" className="space-y-2 rounded border border-border p-2 text-sm">
      {state.isError && <p role="alert">The saved turn state could not be checked.</p>}
      {error && <p role="alert">The set-aside request was not confirmed. Check the saved state before proceeding.</p>}
      {(allowPendingDaemon || allowPendingExpired) && state.data?.status === "pending" && (
        <>
          <p>
            {allowPendingExpired
              ? "This input is no longer retained and no model work has been recorded as started. Setting it aside cancels this input without replaying it."
              : "No model work has been recorded as started for this daemon turn. Setting it aside prevents a later launch."}
          </p>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledgement(event.target.checked ? acknowledgementKey : null)}
            />
            <span>
              {allowPendingExpired
                ? "I understand this expired, unstarted input will be set aside."
                : "I understand this unstarted daemon turn will be set aside."}
            </span>
          </label>
          <Button
            type="button"
            size="sm"
            disabled={!acknowledged || busy || state.isError || state.isFetching}
            onClick={() => void setAside()}
          >
            Set aside this unstarted turn
          </Button>
        </>
      )}
      {state.data?.status === "unresolved" && (
        <>
          <p>This turn has no recorded answer and may still be running. Its possible effects remain unknown.</p>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledgement(event.target.checked ? acknowledgementKey : null)}
            />
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
        <p role="status">This turn was set aside. If it had started, its possible effects remain unknown.</p>
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
