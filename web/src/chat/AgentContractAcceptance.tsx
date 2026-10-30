import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { chatKey } from "@/api/chat";
import { api, problem, type SchemaChatSession, type SchemaScheduledAgentProposal } from "@/api/client";
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

/** Review exact terms through the human action boundary; acceptance grants no execution or access. */
export function AgentContractAcceptance({
  sessionId,
  proposal,
}: {
  sessionId: string;
  proposal: SchemaScheduledAgentProposal;
}) {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const chatId = params.get("contract-chat");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const reviewKey = ["heinzel", "agent-contract-review", sessionId, proposal.request_id] as const;
  const review = useQuery({
    queryKey: reviewKey,
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/agent-contract-review", {
          params: { query: { session_id: sessionId, request_id: proposal.request_id } },
        }),
      );
      if (value.proposal.session_id !== sessionId || canonical(value.proposal) !== canonical(proposal))
        throw new Error("The terms changed");
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
    enabled: chatId !== null,
    retry: false,
  });
  const ownedChat = chat.data?.id === chatId && chat.data.personal_session_id === sessionId ? chat.data : undefined;
  const action = ownedChat?.actions?.find(
    (item) =>
      item.module === "heinzel" &&
      item.operation === "accept-agent-contract" &&
      item.arguments?.session_id === sessionId &&
      item.arguments?.request_id === proposal.request_id &&
      !Object.hasOwn(item.arguments ?? {}, "approval_proof") &&
      canonical(item.arguments?.review) === canonical(proposal),
  );
  const remember = (value: SchemaChatSession, expected?: string) => {
    if (value.personal_session_id !== sessionId || (expected !== undefined && value.id !== expected))
      throw new Error("Unexpected conversation");
    client.setQueryData(chatKey(value.id), value);
  };
  const stage = async () => {
    if (busy || !review.data || review.data.accepted) return;
    setBusy(true);
    setError(false);
    try {
      const value = await result(
        api.POST("/system/runner/chat/propose-agent-contract", {
          params: { query: { durable_session: sessionId, request_id: proposal.request_id } },
        }),
      );
      remember(value);
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("contract-chat", value.id);
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
    if (!ownedChat || !action || busy) return;
    setBusy(true);
    setError(false);
    try {
      if (confirm) {
        const current = await result(
          api.GET("/heinzel/agent-contract-review", {
            params: { query: { session_id: sessionId, request_id: proposal.request_id } },
          }),
        );
        if (
          current.accepted ||
          current.proposal.session_id !== sessionId ||
          canonical(current.proposal) !== canonical(proposal)
        )
          throw new Error("The terms changed");
      }
      const value = await result(
        confirm
          ? api.POST("/system/runner/chat/confirm", { params: { query: { session: ownedChat.id, action: action.id } } })
          : api.POST("/system/runner/chat/decline", {
              params: { query: { session: ownedChat.id, action: action.id } },
            }),
      );
      remember(value, ownedChat.id);
      await client.invalidateQueries({ queryKey: reviewKey });
      await client.invalidateQueries({ queryKey: ["heinzel", "agent-schedule-contract-review", sessionId] });
    } catch {
      setError(true);
      try {
        const value = await result(
          api.GET("/system/runner/chat/show", { params: { query: { session: ownedChat.id } } }),
        );
        remember(value, ownedChat.id);
        await client.invalidateQueries({ queryKey: reviewKey });
        await client.invalidateQueries({ queryKey: ["heinzel", "agent-schedule-contract-review", sessionId] });
      } catch {
        // Keep the review link for an explicit later inspection; never repeat confirmation here.
      }
    } finally {
      setBusy(false);
    }
  };
  const recover = async () => {
    if (!ownedChat || !action || busy) return;
    setBusy(true);
    setError(false);
    try {
      const value = await result(
        api.POST("/system/runner/chat/recover", {
          params: { query: { session: ownedChat.id, action: action.id } },
        }),
      );
      remember(value, ownedChat.id);
      await client.invalidateQueries({ queryKey: reviewKey });
      await client.invalidateQueries({ queryKey: ["heinzel", "agent-schedule-contract-review", sessionId] });
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2">
      <p>Accepting these terms does not activate a schedule or grant access or tools.</p>
      {review.data?.accepted && <p role="status">These exact terms were accepted.</p>}
      {review.isError && (
        <p role="alert">These terms could not be verified. Refresh the conversation to review them again.</p>
      )}
      {error && <p role="alert">The review could not be confirmed. Inspect the saved action before trying again.</p>}
      {chatId && (chat.isError || (chat.data && !ownedChat)) && (
        <p role="alert">The saved review conversation could not be verified.</p>
      )}
      {action?.status === "declined" && <p>These terms were declined. Ask for a new revision to review again.</p>}
      {!review.data?.accepted && !action && !chatId && (
        <Button type="button" size="sm" disabled={busy || !review.data || review.isError} onClick={() => void stage()}>
          Review these terms
        </Button>
      )}
      {!review.data?.accepted && action?.status === "pending" && ownedChat && (
        <ol>
          <ActionCard
            action={action}
            busy={busy}
            proposalTitle="Review agent terms"
            onDecide={(confirm) => void decide(confirm)}
          />
        </ol>
      )}
      {action?.status === "running" && (
        <div role="status">
          <p>Confirmation was interrupted. Check the saved outcome.</p>
          <Button type="button" size="sm" disabled={busy} onClick={() => void recover()}>
            Check outcome
          </Button>
        </div>
      )}
      {action?.status === "uncertain" && (
        <p role="alert">The outcome is uncertain. This action will not be repeated.</p>
      )}
    </div>
  );
}
