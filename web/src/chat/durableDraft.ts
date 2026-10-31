import { useCallback, useEffect, useRef, useState } from "react";
import { registerUpdatePreparation } from "@/updates/work";

const PREFIX = "hz.durable.composer.";
const REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

export type DurableRequest = {
  request_id: string;
  message: string;
  accepted?: boolean;
  outcome?: "subscription_usage_limit";
};
export type DurableComposerState = {
  text: string;
  pending: DurableRequest | null;
  agentPending: DurableRequest | null;
  checkInRequest: string | null;
};

type SavedComposer = { version: 1; session_id: string } & DurableComposerState;
const empty = (): DurableComposerState => ({ text: "", pending: null, agentPending: null, checkInRequest: null });

function isRequest(value: unknown): value is DurableRequest {
  if (!value || typeof value !== "object") return false;
  const request = value as Partial<DurableRequest>;
  return (
    typeof request.request_id === "string" &&
    REQUEST_ID.test(request.request_id) &&
    typeof request.message === "string" &&
    request.message.length > 0 &&
    request.message.length <= 4000 &&
    (request.accepted === undefined || typeof request.accepted === "boolean") &&
    (request.outcome === undefined || (request.outcome === "subscription_usage_limit" && request.accepted === true))
  );
}

function read(key: string, sessionId: string): { state: DurableComposerState; error: boolean } {
  try {
    const raw = sessionStorage.getItem(key);
    if (raw === null) return { state: empty(), error: false };
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object") return { state: empty(), error: true };
    const saved = value as Partial<SavedComposer>;
    if (
      saved.version !== 1 ||
      saved.session_id !== sessionId ||
      typeof saved.text !== "string" ||
      saved.text.length > 4000 ||
      (saved.pending !== null && !isRequest(saved.pending)) ||
      (saved.agentPending !== null && !isRequest(saved.agentPending)) ||
      (saved.checkInRequest !== null &&
        (typeof saved.checkInRequest !== "string" || !REQUEST_ID.test(saved.checkInRequest)))
    )
      return { state: empty(), error: true };
    return {
      state: {
        text: saved.text,
        pending: saved.pending ?? null,
        agentPending: saved.agentPending ?? null,
        checkInRequest: saved.checkInRequest ?? null,
      },
      error: false,
    };
  } catch {
    return { state: empty(), error: true };
  }
}

function hasWork(state: DurableComposerState): boolean {
  return Boolean(state.text || state.pending || state.agentPending || state.checkInRequest);
}

function write(key: string, sessionId: string, state: DurableComposerState): void {
  if (!hasWork(state)) {
    sessionStorage.removeItem(key);
    if (sessionStorage.getItem(key) !== null) throw new Error("The saved message could not be cleared.");
    return;
  }
  const value: SavedComposer = { version: 1, session_id: sessionId, ...state };
  const encoded = JSON.stringify(value);
  sessionStorage.setItem(key, encoded);
  if (sessionStorage.getItem(key) !== encoded) throw new Error("The message could not be preserved in this tab.");
}

/** Keep unsent and idempotently retryable work in this tab, scoped to one durable session. */
export function useDurableDraft(sessionId: string) {
  const key = `${PREFIX}${sessionId}`;
  const [initial] = useState(() => read(key, sessionId));
  const [state, setState] = useState(initial.state);
  const stateRef = useRef(state);
  const [storageError, setStorageError] = useState(initial.error);
  const [recoveryBlocked, setRecoveryBlocked] = useState(initial.error);

  const update = useCallback(
    (patch: Partial<DurableComposerState>): boolean => {
      if (recoveryBlocked) return false;
      const next = { ...stateRef.current, ...patch };
      stateRef.current = next;
      setState(next);
      try {
        write(key, sessionId, next);
        setStorageError(false);
        return true;
      } catch {
        setStorageError(true);
        return false;
      }
    },
    [key, recoveryBlocked, sessionId],
  );

  const resolveRecovery = useCallback((): boolean => {
    try {
      sessionStorage.removeItem(key);
      if (sessionStorage.getItem(key) !== null) return false;
      const next = empty();
      stateRef.current = next;
      setState(next);
      setStorageError(false);
      setRecoveryBlocked(false);
      return true;
    } catch {
      setStorageError(true);
      return false;
    }
  }, [key]);

  useEffect(
    () =>
      registerUpdatePreparation(() => {
        if (recoveryBlocked || storageError)
          throw new Error("The durable message could not be preserved; resolve it before updating.");
        write(key, sessionId, stateRef.current);
      }),
    [key, recoveryBlocked, sessionId, storageError],
  );

  return { state, update, storageError, recoveryBlocked, resolveRecovery };
}
