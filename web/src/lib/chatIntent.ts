// Deliberate chat entry points select the shared workspace before focusing its retained composer.
import { type RefObject, useEffect } from "react";

export const OPEN_CHAT = "hz:open-chat";
export const OPEN_NEW_CHAT = "hz:open-new-chat";
const FOCUS_COMPOSER = "hz:focus-composer";
/** How long a New Chat waits for the chat it opens to mount and load before its focus request lapses. */
const FOCUS_WINDOW_MS = 5000;
let focusUntil = 0;

/** Ask the chat's composer (or the first start's name field) for focus, now or once it mounts and is ready. */
export function requestComposerFocus() {
  focusUntil = Date.now() + FOCUS_WINDOW_MS;
  window.dispatchEvent(new Event(FOCUS_COMPOSER));
}

/**
 * Focus `ref` when New Chat asks for it: at once when it is mounted and `ready`, else when it mounts or becomes ready
 * within the request's window. A field that mounts takes the request; one already shown keeps it open for the chat
 * that replaces it (New Chat from a linked Heinzel's chat opens the default one).
 */
export function useComposerFocus(ref: RefObject<HTMLInputElement | HTMLTextAreaElement | null>, ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const focus = () => {
      if (Date.now() >= focusUntil || !ref.current) return false;
      ref.current.focus();
      return true;
    };
    if (focus()) focusUntil = 0;
    window.addEventListener(FOCUS_COMPOSER, focus);
    return () => window.removeEventListener(FOCUS_COMPOSER, focus);
  }, [ref, ready]);
}

/** A Heinzel's chat (`?chat=hz-<ULID>`); every chat in the app is with a Heinzel (ADR 0157). */
export const isDurableSession = (id: string) => /^hz-[0-9A-HJKMNP-TV-Z]{26}$/.test(id);

/** Leave the linked personal conversation and any nested chat. */
export function clearChatLink(params: URLSearchParams) {
  for (const key of [
    "chat",
    "agent-chat",
    "approval-chat",
    "approval-request",
    "agent-open-request",
    "contract-chat",
    "schedule-chat",
    "schedule-request",
    "notification-chat",
    "connected-chat",
  ])
    params.delete(key);
}

/** The new Heinzel's draft on its page (`?create=1`), keeping the linked personal conversation (`?chat=`). */
export function createDraftPath(current: URLSearchParams) {
  const next = new URLSearchParams();
  const chat = current.get("chat");
  if (chat) next.set("chat", chat);
  next.set("create", "1");
  return `/heinzel?${next}`;
}
