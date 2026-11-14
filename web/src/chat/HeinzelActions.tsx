// What a Heinzel's turn proposed with its tools, as cards in its chat: each runs only on Confirm (ADR 0045, 0162).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, problem, type SchemaChatSession } from "@/api/client";
import { ActionCard } from "@/chat/ActionCard";
import { t } from "@/i18n";

async function result<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok && data !== undefined) return data;
  throw problem(response.status, error);
}

/** The Heinzel's actions chats, checked to be this Heinzel's own: a chat bound elsewhere is never shown. */
function own(chats: SchemaChatSession[], sessionId: string): SchemaChatSession[] {
  if (chats.some((chat) => chat.personal_session_id !== sessionId || chat.context?.kind !== "heinzel"))
    throw new Error("This conversation does not belong to the selected Heinzel.");
  return chats;
}

export function heinzelActionsKey(sessionId: string) {
  return ["system", "runner", "chat", "heinzel-actions", sessionId];
}

export function HeinzelActions({ sessionId, refresh }: { sessionId: string; refresh: unknown }) {
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const chats = useQuery({
    queryKey: [...heinzelActionsKey(sessionId), refresh],
    queryFn: async () =>
      own(
        await result(
          api.GET("/system/runner/chat/heinzel-actions", { params: { query: { durable_session: sessionId } } }),
        ),
        sessionId,
      ),
    retry: false,
  });
  const pending = (chats.data ?? []).flatMap((chat) =>
    (chat.actions ?? []).filter((action) => action.status === "pending").map((action) => ({ chat, action })),
  );
  if (chats.isError) return <p role="alert">{t("durable.heinzelActionsUnavailable")}</p>;
  if (pending.length === 0) return null;
  return (
    <section aria-label={t("durable.heinzelActionsTitle")} className="space-y-2">
      {error && <p role="alert">{t("durable.heinzelActionFailed")}</p>}
      <ol className="space-y-2">
        {pending.map(({ chat, action }) => (
          <ActionCard
            key={`${chat.id}-${action.id}`}
            action={action}
            busy={busy}
            onDecide={(confirm) => {
              if (busy) return;
              void (async () => {
                setBusy(true);
                setError(false);
                try {
                  const query = { params: { query: { session: chat.id, action: action.id } } };
                  const updated = await result(
                    confirm
                      ? api.POST("/system/runner/chat/confirm", query)
                      : api.POST("/system/runner/chat/decline", query),
                  );
                  const recorded = updated.actions?.find((item) => item.id === action.id);
                  if (
                    updated.id !== chat.id ||
                    recorded?.module !== action.module ||
                    recorded.operation !== action.operation ||
                    JSON.stringify(recorded.arguments) !== JSON.stringify(action.arguments)
                  )
                    throw new Error("The response did not match the reviewed action.");
                } catch {
                  setError(true);
                } finally {
                  setBusy(false);
                  void client.invalidateQueries({ queryKey: heinzelActionsKey(sessionId) });
                }
              })();
            }}
          />
        ))}
      </ol>
    </section>
  );
}
