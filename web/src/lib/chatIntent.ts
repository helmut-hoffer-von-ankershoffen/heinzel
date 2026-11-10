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
  ])
    params.delete(key);
}
