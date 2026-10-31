import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { chatKey } from "@/api/chat";
import { api, problem, type SchemaChatSession } from "@/api/client";
import type { components } from "@/api/schema";
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
export function AgentScheduleReview({
  sessionId,
  proposal,
}: {
  sessionId: string;
  proposal: components["schemas"]["ScheduleBinding"];
}) {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const chatId = params.get("schedule-request") === proposal.request_id ? params.get("schedule-chat") : null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const reviewKey = ["heinzel", "agent-schedule", sessionId, proposal.request_id] as const;
  const review = useQuery({
    queryKey: reviewKey,
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/agent-schedule", {
          params: { query: { session_id: sessionId, request_id: proposal.request_id } },
        }),
      );
      if (value.binding.session_id !== sessionId || canonical(value.binding) !== canonical(proposal))
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
      item.operation === "approve-agent-schedule" &&
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
    if (busy || !review.data || review.data.status !== "proposed") return;
    setBusy(true);
    setError(false);
    try {
      const value = await result(
        api.POST("/system/runner/chat/propose-agent-schedule-review", {
          params: { query: { durable_session: sessionId, request_id: proposal.request_id } },
        }),
      );
      remember(value);
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("schedule-chat", value.id);
          next.set("schedule-request", proposal.request_id);
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
          api.GET("/heinzel/agent-schedule", {
            params: { query: { session_id: sessionId, request_id: proposal.request_id } },
          }),
        );
        if (
          current.status !== "proposed" ||
          current.binding.session_id !== sessionId ||
          canonical(current.binding) !== canonical(proposal)
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
      await client.invalidateQueries({ queryKey: ["heinzel", "agent-schedules", sessionId] });
    } catch {
      setError(true);
      try {
        const value = await result(
          api.GET("/system/runner/chat/show", { params: { query: { session: ownedChat.id } } }),
        );
        remember(value, ownedChat.id);
        await client.invalidateQueries({ queryKey: reviewKey });
        await client.invalidateQueries({ queryKey: ["heinzel", "agent-schedules", sessionId] });
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
      await client.invalidateQueries({ queryKey: ["heinzel", "agent-schedules", sessionId] });
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2">
      <p>
        Confirmation permits recurring model turns under these exact terms. The first turn may run in the current
        interval after approval; the daemon must be running.
        {proposal.connected_grant
          ? ` These turns may use the separately approved ${proposal.connected_grant.capability}.${proposal.connected_grant.method} read for ${proposal.connected_grant.review.account_display || proposal.connected_grant.review.account_identity}, with the exact source, scopes and argument policy in this review. A rolling policy derives a new UTC window for each read. It stops if the grant or source changes.`
          : " It grants no external tools or access."}
      </p>
      {proposal.connected_grant?.review.candidate_read_disclosure && (
        <p className="break-words">
          Provider-reported candidate read: {proposal.connected_grant.review.candidate_read_disclosure}
        </p>
      )}
      {proposal.notification_policy && (
        <div className="space-y-1 break-words">
          <p>
            Local notifications are included: at most {proposal.notification_policy.max_per_24h} attempts per rolling 24
            hours and one per turn. Content may appear on this device; queued does not mean shown.
          </p>
          {proposal.notification_policy.item_evidence ? (
            <>
              <p>
                Only an item observed in the current turn can trigger a notice; at most one attempt per source
                {proposal.notification_policy.item_evidence.deduplicate_by === "revision" ? " item revision" : " item"}
                for this approved source and contract. The item identity is provider-reported and does not establish
                complete source history.
              </p>
              {proposal.connected_grant?.review.item_identity && (
                <p>
                  Provider-reported collection{" "}
                  {proposal.connected_grant.review.item_identity.collection.join(".") || "result array"}, identity field{" "}
                  {proposal.connected_grant.review.item_identity.identity_field}
                  {proposal.connected_grant.review.item_identity.revision_field
                    ? `, revision field ${proposal.connected_grant.review.item_identity.revision_field}`
                    : ""}
                  .
                </p>
              )}
            </>
          ) : (
            <p>
              At least {proposal.notification_policy.topic_cooldown_seconds} seconds between the same model-chosen
              topic.
            </p>
          )}
        </div>
      )}
      {review.data?.status === "approved" && (
        <p role="status">This schedule was approved. Approval alone does not indicate that the daemon is running.</p>
      )}
      {review.isError && (
        <p role="alert">These terms could not be verified. Refresh the conversation to review them again.</p>
      )}
      {error && <p role="alert">The review could not be confirmed. Inspect the saved action before trying again.</p>}
      {chatId && (chat.isError || (chat.data && !ownedChat)) && (
        <p role="alert">The saved review conversation could not be verified.</p>
      )}
      {ownedChat && !action && <p role="alert">The saved schedule review does not match these exact terms.</p>}
      {action?.status === "declined" && <p>This schedule was declined. Create a new proposal to review again.</p>}
      {review.data?.status === "proposed" && !action && !chatId && (
        <Button type="button" size="sm" disabled={busy || !review.data || review.isError} onClick={() => void stage()}>
          Review this schedule
        </Button>
      )}
      {review.data?.status === "proposed" && action?.status === "pending" && ownedChat && (
        <ol>
          <ActionCard
            action={action}
            busy={busy}
            proposalTitle="Review agent schedule"
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
