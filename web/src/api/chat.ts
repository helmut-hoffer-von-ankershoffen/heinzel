// The chat's operations (`hz chat`, system.runner.chat, ADR 0045): sessions kept by hz, answers streamed, and actions
// the agent proposed confirmed or declined by the person.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type SchemaChatSession, unwrap } from "./client";
import { type StreamEvent, stream } from "./stream";

export const chatKey = (id: string) => ["system", "runner", "chat", "show", id] as const;

export const useChatSession = (id: string | null) =>
  useQuery({
    queryKey: chatKey(id ?? ""),
    queryFn: () => unwrap(api.GET("/system/runner/chat/show", { params: { query: { session: id ?? "" } } })),
    enabled: id != null,
    retry: false,
  });

/** A new session about `context` (`event:<id>`, `route:/doctor`); `detail` is what the opener knows, in words. */
export async function openChat(context: string | null, detail?: string): Promise<SchemaChatSession> {
  return unwrap(api.POST("/system/runner/chat/open", { params: { query: { context, detail: detail || null } } }));
}

export async function sendChat(
  session: string,
  message: string,
  onEvent: (event: StreamEvent) => void,
): Promise<SchemaChatSession> {
  return stream<SchemaChatSession>("/system/runner/chat/send", { session, message }, onEvent);
}

/** Confirm or decline an action; the answer is the session, which replaces the cached one. */
export const useDecide = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ session, action, confirm }: { session: string; action: string; confirm: boolean }) =>
      unwrap(
        confirm
          ? api.POST("/system/runner/chat/confirm", { params: { query: { session, action } } })
          : api.POST("/system/runner/chat/decline", { params: { query: { session, action } } }),
      ),
    onSuccess: (chat) => client.setQueryData(chatKey(chat.id), chat),
  });
};
