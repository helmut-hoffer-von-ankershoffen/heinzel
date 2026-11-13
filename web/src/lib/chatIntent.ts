// Deliberate chat entry points select the shared workspace before focusing its retained composer.
export const OPEN_CHAT = "hz:open-chat";
export const OPEN_NEW_CHAT = "hz:open-new-chat";

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
    "profile-chat",
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

/** The Heinzel's chat with its Details open on the name and look: the one place to edit them. */
export function editInChatPath(sessionId: string) {
  const id = encodeURIComponent(sessionId);
  return `/heinzel?chat=${id}&profile-chat=${id}`;
}

// The composer's "+" hands its message to the new Heinzel's draft; the draft puts it in the new chat's composer.
const DRAFT_MESSAGE = "hz.personal-heinzel.new-draft-message";

/** Keep this message for the draft; false when this tab cannot keep it, so the composer keeps it instead. */
export function handDraftMessage(message: string): boolean {
  if (!message.trim() || message.length > 4000) return false;
  try {
    sessionStorage.setItem(DRAFT_MESSAGE, message);
    return sessionStorage.getItem(DRAFT_MESSAGE) === message;
  } catch {
    return false;
  }
}

/** The message waiting for the draft, if any. */
export function readDraftMessage(): string | null {
  try {
    const message = sessionStorage.getItem(DRAFT_MESSAGE);
    return message?.trim() ? message : null;
  } catch {
    return null;
  }
}

/** Forget the draft's message (discarded, or now in the new chat's composer). */
export function clearDraftMessage() {
  try {
    sessionStorage.removeItem(DRAFT_MESSAGE);
  } catch {
    // An unreadable store holds nothing to forget.
  }
}

/** Put the draft's message into the new Heinzel's composer, as the chat reads it; false if the tab cannot keep it. */
export function takeDraftMessage(sessionId: string, message: string): boolean {
  try {
    const key = `hz.durable.composer.${sessionId}`;
    const encoded = JSON.stringify({
      version: 1,
      session_id: sessionId,
      text: message,
      pending: null,
      agentPending: null,
      checkInRequest: null,
    });
    const raw = sessionStorage.getItem(key);
    if (raw !== null && raw !== encoded) return false;
    sessionStorage.setItem(key, encoded);
    if (sessionStorage.getItem(key) !== encoded) return false;
    clearDraftMessage();
    return true;
  } catch {
    return false;
  }
}
