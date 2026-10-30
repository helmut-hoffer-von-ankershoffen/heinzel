import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { chatKey } from "@/api/chat";
import { api, problem, type SchemaChatSession, type SchemaNotificationProposal } from "@/api/client";
import { ActionCard } from "@/chat/Conversation";
import { Button } from "@/components/ui/button";

async function result<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok && data !== undefined) return data;
  throw problem(response.status, error);
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  return JSON.stringify(value) ?? "undefined";
}

/** Owner-private notification drafts; listing and review do not send a notification. */
export function NotificationPanel({ sessionId }: { sessionId: string }) {
  const [cursor, setCursor] = useState(0);
  const page = useQuery({
    queryKey: ["heinzel", "notification-proposals", sessionId, cursor],
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/notification-proposals", { params: { query: { session_id: sessionId, cursor, limit: 20 } } }),
      );
      if (
        value.session_id !== sessionId ||
        value.items.some((item) => item.seq <= cursor) ||
        (value.next_cursor != null && value.next_cursor <= cursor)
      )
        throw new Error("Unexpected notification page");
      return value;
    },
    retry: false,
  });
  return (
    <section aria-label="Notification drafts" className="space-y-2 rounded border border-border p-3 text-sm">
      <h3 className="font-medium">Notification drafts</h3>
      <p>Drafts need your review before they are queued. Queued does not mean shown.</p>
      <Button size="sm" variant="outline" onClick={() => void page.refetch()}>
        Refresh drafts
      </Button>
      {page.isError && <p role="alert">Notification drafts could not be verified.</p>}
      {page.data?.items.length === 0 && <p>No drafts in this page.</p>}
      {page.data?.items.map((item) => (
        <NotificationDraft key={item.request_id} sessionId={sessionId} requestId={item.request_id} />
      ))}
      <div className="flex gap-2">
        {cursor > 0 && (
          <Button type="button" size="sm" variant="outline" onClick={() => setCursor(0)}>
            First drafts
          </Button>
        )}
        {page.data?.next_cursor != null && (
          <Button type="button" size="sm" variant="outline" onClick={() => setCursor(page.data?.next_cursor ?? 0)}>
            More drafts
          </Button>
        )}
      </div>
    </section>
  );
}

function NotificationDraft({ sessionId, requestId }: { sessionId: string; requestId: string }) {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const chatId = params.get("notification-chat");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const draft = useQuery({
    queryKey: ["heinzel", "notification-proposal", sessionId, requestId],
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/notification-proposal", {
          params: { query: { session_id: sessionId, request_id: requestId } },
        }),
      );
      if (value.session_id !== sessionId || value.request_id !== requestId || value.status !== "proposed")
        throw new Error("Unexpected draft");
      return value;
    },
    retry: false,
  });
  const chat = useQuery({
    queryKey: chatKey(chatId ?? ""),
    queryFn: async () => {
      const value = await result(api.GET("/system/runner/chat/show", { params: { query: { session: chatId ?? "" } } }));
      if (value.id !== chatId || value.personal_session_id !== sessionId) throw new Error("Unexpected conversation");
      return value;
    },
    enabled: !!chatId,
    retry: false,
  });
  const owned = chat.data?.id === chatId && chat.data.personal_session_id === sessionId ? chat.data : undefined;
  const action = owned?.actions?.find(
    (item) =>
      item.module === "heinzel" &&
      item.operation === "queue-agent-notification" &&
      item.arguments?.session_id === sessionId &&
      item.arguments?.request_id === requestId &&
      !Object.hasOwn(item.arguments ?? {}, "approval_proof") &&
      canonical(item.arguments?.review) === canonical(draft.data),
  );
  const remember = (value: SchemaChatSession, expected?: string) => {
    if (value.personal_session_id !== sessionId || (expected && value.id !== expected))
      throw new Error("Unexpected conversation");
    client.setQueryData(chatKey(value.id), value);
  };
  const stage = async () => {
    if (!draft.data || busy) return;
    setBusy(true);
    setError(false);
    try {
      const value = await result(
        api.POST("/system/runner/chat/propose-agent-notification", {
          params: { query: { durable_session: sessionId, request_id: requestId } },
        }),
      );
      remember(value);
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("notification-chat", value.id);
          return next;
        },
        { replace: true },
      );
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  const decide = async (confirm: boolean) => {
    if (!owned || !action || !draft.data || busy) return;
    setBusy(true);
    setError(false);
    try {
      if (confirm) {
        const current = await result(
          api.GET("/heinzel/notification-proposal", {
            params: { query: { session_id: sessionId, request_id: requestId } },
          }),
        );
        if (
          current.session_id !== sessionId ||
          current.request_id !== requestId ||
          canonical(current) !== canonical(draft.data)
        )
          throw new Error("Draft changed");
      }
      const value = await result(
        confirm
          ? api.POST("/system/runner/chat/confirm", { params: { query: { session: owned.id, action: action.id } } })
          : api.POST("/system/runner/chat/decline", { params: { query: { session: owned.id, action: action.id } } }),
      );
      remember(value, owned.id);
    } catch {
      setError(true);
      try {
        remember(
          await result(api.GET("/system/runner/chat/show", { params: { query: { session: owned.id } } })),
          owned.id,
        );
      } catch {
        /* Do not repeat an uncertain decision. */
      }
    } finally {
      setBusy(false);
    }
  };
  const inspect = async () => {
    if (!owned || !action || busy) return;
    setBusy(true);
    setError(false);
    try {
      const status = await result(
        api.GET("/heinzel/notification-outcome", {
          params: { query: { session_id: sessionId, request_id: requestId } },
        }),
      );
      if (status !== "recorded" && status !== "uncertain") throw new Error("Unexpected outcome");
      setOutcome(status);
      if (action.status === "running") {
        const value = await result(
          api.POST("/system/runner/chat/recover", { params: { query: { session: owned.id, action: action.id } } }),
        );
        remember(value, owned.id);
      }
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2 rounded border border-border p-2">
      {draft.isError && <p role="alert">This notification draft could not be verified.</p>}
      {chatId && chat.isError && <p role="alert">The saved review conversation could not be verified.</p>}
      {error && (
        <p role="alert">
          The outcome could not be verified. Inspect the saved action; it will not be retried automatically.
        </p>
      )}
      {draft.data && <DraftTerms proposal={draft.data} />}
      {draft.data && !action && !chat.isError && (
        <Button type="button" size="sm" disabled={busy || chat.isFetching} onClick={() => void stage()}>
          Review notification
        </Button>
      )}
      {action?.status === "pending" && (
        <ol>
          <ActionCard
            action={action}
            busy={busy}
            proposalTitle="Review notification"
            onDecide={(confirm) => void decide(confirm)}
          />
        </ol>
      )}
      {action?.status === "done" && <p role="status">Queued under local policy; display is not confirmed.</p>}
      {action?.status === "declined" && <p>Notification declined.</p>}
      {(action?.status === "uncertain" || action?.status === "running") && (
        <div role="alert">
          <p>Outcome uncertain; no automatic retry.</p>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void inspect()}>
            Check notification outcome
          </Button>
        </div>
      )}
      {outcome && (
        <p role="status">
          {outcome === "recorded"
            ? "Queued; display is not confirmed."
            : "Still uncertain; no notification was repeated."}
        </p>
      )}
    </div>
  );
}
function DraftTerms({ proposal }: { proposal: SchemaNotificationProposal }) {
  return (
    <div className="space-y-1 break-words">
      <p className="font-medium">{proposal.title}</p>
      <p className="whitespace-pre-wrap">{proposal.body}</p>
      <p>Topic: {proposal.topic} (model-proposed, not verified source identity)</p>
      <p>{proposal.disclosure}</p>
    </div>
  );
}
