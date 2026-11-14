// Whether a Heinzel's conversation has a reply on its way, shared between its chat and its page: a review on the page
// (source access, a write, a draft share) waits while the chat's turn is unsettled, as it did beside the composer.
import { useSyncExternalStore } from "react";

const busy = new Set<string>();
const listeners = new Set<() => void>();

export function setConversationBusy(sessionId: string, value: boolean) {
  if (busy.has(sessionId) === value) return;
  if (value) busy.add(sessionId);
  else busy.delete(sessionId);
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useConversationBusy(sessionId: string): boolean {
  return useSyncExternalStore(subscribe, () => busy.has(sessionId));
}
