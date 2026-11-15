// The chat's operations (`hz chat`, system.runner.chat, ADR 0045): the scoped chats a Heinzel's reviews open are kept
// by hz, their answers streamed, and actions the agent proposed confirmed or declined by the person.
import type { SchemaChatSession } from "./client";
import { type StreamEvent, stream } from "./stream";

export const chatKey = (id: string) => ["system", "runner", "chat", "show", id] as const;

/**
 * Send one message. The web chat has no model picker (ADR 0158): every message asks for the default model and
 * effort, which also clears a per-chat model an earlier release's picker left on the session.
 */
export async function sendChat(
  session: string,
  message: string,
  onEvent: (event: StreamEvent) => void,
  requestId?: string,
): Promise<SchemaChatSession> {
  return stream<SchemaChatSession>(
    "/system/runner/chat/send",
    { session, message, model: "", effort: "", request_id: requestId },
    onEvent,
  );
}
