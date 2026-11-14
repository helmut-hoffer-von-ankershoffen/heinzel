// The chat's operations (`hz chat`, system.runner.chat, ADR 0045): the scoped chats a Heinzel's reviews open are kept
// by hz, their answers streamed, and actions the agent proposed confirmed or declined by the person.
import type { SchemaChatSession } from "./client";
import { type StreamEvent, stream } from "./stream";

export const chatKey = (id: string) => ["system", "runner", "chat", "show", id] as const;

/** The model and effort a turn asks for; kept by the session for its later messages too. */
export type ModelChoice = { model?: string | null; effort?: string | null };

export async function sendChat(
  session: string,
  message: string,
  onEvent: (event: StreamEvent) => void,
  choice: ModelChoice = {},
  requestId?: string,
): Promise<SchemaChatSession> {
  return stream<SchemaChatSession>(
    "/system/runner/chat/send",
    { session, message, model: choice.model, effort: choice.effort, request_id: requestId },
    onEvent,
  );
}
