// The chat's own review of what a helper proposed: one card with everything it would do, and one button to start it.
// The button is the person's decision. It walks the same typed confirmations every other head uses, each bound to the
// exact bytes shown here, so a change between reading and clicking fails rather than slips through.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, problem } from "@/api/client";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { Button } from "@/components/ui/button";
import { notificationPolicy } from "./ScheduledNotificationConsent";

const MAX_REQUESTS = 4;
const MAX_TIMEOUT = 120;

async function result<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok && data !== undefined) return data;
  throw problem(response.status, error);
}

type Snapshot = {
  session_id: string;
  name: string;
  profile_revision?: number | null;
  messages?: unknown[] | null;
};

/** "Every hour", "Every 5 minutes", in the words a person would use. */
export function cadenceWords(seconds: number): string {
  const minutes = seconds / 60;
  if (minutes === 60) return "Every hour";
  if (minutes === 1) return "Every minute";
  if (minutes % 60 === 0) return `Every ${minutes / 60} hours`;
  return `Every ${minutes} minutes`;
}

/** Promise only the deduplication the source can back: a source reporting no identities or revisions gets less. */
function noticeMode(
  asked: "item" | "revision" | null | undefined,
  source: { review: { item_identity?: { revision_field?: string | null } | null } } | null,
): "topic" | "item" | "revision" {
  const identity = source?.review.item_identity;
  if (!asked || !identity) return "topic";
  return asked === "revision" && !identity.revision_field ? "item" : asked;
}

function windowWords(seconds: number): string {
  if (seconds >= 7200) return `${Math.round(seconds / 3600)} hours`;
  if (seconds === 3600) return "an hour";
  return `${Math.round(seconds / 60)} minutes`;
}

async function confirmStaged(
  chat: { id: string; actions?: { id: string; operation: string; status?: string }[] | null },
  operation: string,
) {
  const action = chat.actions?.find((item) => item.operation === operation && item.status === "pending");
  if (!action) throw new Error("There is nothing to confirm");
  const updated = await result(
    api.POST("/system/runner/chat/confirm", { params: { query: { session: chat.id, action: action.id } } }),
  );
  const decided = updated.actions?.find((item) => item.id === action.id);
  if (decided?.status !== "done") throw new Error(decided?.result || "This was not confirmed.");
}

type PendingDraft = {
  request_id: string;
  reference: string;
  section: string;
  account: string;
  content: string;
};

/** A proposed edit to a document, shown in the chat where it was made. Only "Write it" reaches the document. */
function DraftReview({ sessionId, draft, onDone }: { sessionId: string; draft: PendingDraft; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const approve = async () => {
    setBusy(true);
    setFailed(null);
    try {
      const chat = await result(
        api.POST("/system/runner/chat/propose-captured-write", {
          params: { query: { durable_session: sessionId, request_id: draft.request_id } },
        }),
      );
      await confirmStaged(chat, "execute-captured-write");
    } catch (reason) {
      setFailed(reason instanceof Error ? reason.message : "This could not be written.");
    } finally {
      setBusy(false);
      onDone();
    }
  };
  const discard = async () => {
    setBusy(true);
    try {
      await result(
        api.POST("/heinzel/discard-captured-write", { body: { session_id: sessionId, request_id: draft.request_id } }),
      );
    } catch (reason) {
      setFailed(reason instanceof Error ? reason.message : "This could not be set aside.");
    } finally {
      setBusy(false);
      onDone();
    }
  };
  return (
    <section aria-label="Review this edit" className="space-y-2 rounded-lg border border-border p-3 text-sm">
      <h3 className="font-medium">Review this edit</h3>
      {draft.reference.startsWith("new:") ? (
        <p>
          A new private {draft.reference.split(":")[1]} in your Drive, “{draft.section}”, as {draft.account}.
        </p>
      ) : (
        <p>
          Into{" "}
          <a className="underline" href={draft.reference} target="_blank" rel="noreferrer">
            this file
          </a>
          , {draft.section === "end" ? "at its end" : `after “${draft.section}”`}, as {draft.account}. If it changes
          before you decide, nothing is written.
        </p>
      )}
      <pre className="whitespace-pre-wrap break-words rounded bg-background p-2 text-xs">{draft.content}</pre>
      {failed && (
        <p role="alert" className="text-bad">
          {failed}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="button" size="sm" disabled={busy} onClick={() => void approve()}>
          {busy ? "Writing…" : "Write it"}
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void discard()}>
          Set aside
        </Button>
      </div>
    </section>
  );
}

/** Edits this helper proposed and those it made; receipts come from the provider's reply, never a guess. */
export function DocumentDrafts({ snapshot }: { snapshot: Snapshot }) {
  const sessionId = snapshot.session_id;
  const client = useQueryClient();
  const key = ["heinzel", "document-drafts", sessionId, snapshot.messages?.length ?? 0] as const;
  const drafts = useQuery({
    queryKey: key,
    queryFn: async () => {
      const [pending, activity] = await Promise.all([
        result(api.GET("/heinzel/pending-captured-writes", { params: { query: { session_id: sessionId } } })),
        result(
          api.GET("/heinzel/captured-write-activity-page", {
            params: { query: { session_id: sessionId, limit: 20 } },
          }),
        ),
      ]);
      return { pending: pending as unknown as PendingDraft[], activity: activity.items };
    },
    retry: false,
  });
  const refresh = () => void client.invalidateQueries({ queryKey: ["heinzel", "document-drafts", sessionId] });
  return (
    <>
      {drafts.data?.activity
        .filter((item) => item.status !== "draft")
        .map((item) => (
          <p key={item.seq} role="status" className="rounded-lg border border-border p-3 text-sm">
            {item.status === "acknowledged" ? "Written to " : "Outcome unknown for "}
            <a className="underline" href={item.reference} target="_blank" rel="noreferrer">
              the document
            </a>
            {item.status === "acknowledged"
              ? ` as ${item.account}. Google confirmed this edit.`
              : ". Check the document before asking again; it won't be repeated."}
          </p>
        ))}
      {drafts.data?.pending.map((draft) => (
        <DraftReview key={draft.request_id} sessionId={sessionId} draft={draft} onDone={refresh} />
      ))}
    </>
  );
}

export function AgentSetupCard({ snapshot }: { snapshot: Snapshot }) {
  const sessionId = snapshot.session_id;
  const client = useQueryClient();
  const turns = snapshot.messages?.length ?? 0;
  const [busy, setBusy] = useState(false);
  const [problemText, setProblemText] = useState<string | null>(null);
  const setup = useQuery({
    queryKey: ["heinzel", "agent-setup", sessionId, turns],
    queryFn: async () => {
      const query = { params: { query: { session_id: sessionId } } };
      const [terms, schedule, look, schedules, source, reads] = await Promise.all([
        result(api.GET("/heinzel/latest-scheduled-agent-proposal", query)),
        result(api.GET("/heinzel/latest-schedule-suggestion-view", query)),
        result(api.GET("/heinzel/latest-profile-proposal", query)),
        result(
          api.GET("/heinzel/agent-schedules", { params: { query: { session_id: sessionId, cursor: 0, limit: 100 } } }),
        ),
        result(api.GET("/heinzel/latest-source-suggestion-view", query)),
        result(api.GET("/heinzel/connected-read-state", query)),
      ]);
      // Looking at the account a source would use reads no data and approves nothing; the card shows what it found.
      let sourceReview = null;
      let sourceProblem = null;
      if (source) {
        const request = `source-${source.request_id.slice(5, 69)}`;
        if (reads.proposal?.request_id === request) sourceReview = reads.proposal;
        else
          try {
            sourceReview = await result(
              api.POST("/heinzel/prepare-suggested-source", {
                body: {
                  session_id: sessionId,
                  request_id: request,
                  suggestion_request: source.request_id,
                  suggestion_revision: source.revision,
                },
              }),
            );
          } catch (reason) {
            sourceProblem = reason instanceof Error ? reason.message : "This source could not be checked.";
          }
      }
      const review = terms
        ? await result(
            api.GET("/heinzel/agent-contract-review", {
              params: { query: { session_id: sessionId, request_id: terms.request_id } },
            }),
          )
        : null;
      return {
        terms,
        schedule,
        look,
        schedules: schedules.items,
        accepted: review?.accepted ?? false,
        source,
        sourceReview,
        sourceProblem,
        sourceApproved:
          !!sourceReview && reads.status === "approved" && reads.proposal?.request_id === sourceReview.request_id,
      };
    },
    retry: false,
  });
  const data = setup.data;
  if (setup.isError)
    return (
      <p role="alert" className="text-sm">
        This helper's proposal could not be read. Reload to try again.
      </p>
    );
  if (!data) return null;
  const { terms, schedule, look, schedules, accepted, source, sourceReview, sourceProblem, sourceApproved } = data;
  const interval = schedule && "kind" in schedule.trigger ? schedule.trigger : null;
  const requestId = terms && schedule ? `setup-${schedule.request_id.slice(5, 37)}-${terms.revision}` : null;
  const active = schedules.find((item) => item.status === "approved");
  const running = !!active && active.binding.request_id === requestId;
  const lookPending = !!look && look.expected_revision === (snapshot.profile_revision ?? 0) && look.name !== undefined;
  const termsPending = !!terms && !accepted;
  const sourcePending = !!source && !sourceApproved;
  const schedulePending = !!interval && !!terms && !running;
  if (!termsPending && !schedulePending && !lookPending && !sourcePending) {
    if (!active) return null;
    return (
      <section aria-label="Running" className="rounded-lg border border-border p-3 text-sm">
        <p>
          <strong>{snapshot.name}</strong> is running.{" "}
          {active.binding.wake_on_new_items
            ? `Only when something new arrives, checked ${cadenceWords(active.binding.cadence_seconds).toLowerCase()}`
            : cadenceWords(active.binding.cadence_seconds)}
          {active.binding.quiet_hours
            ? `, quiet ${active.binding.quiet_hours.start} to ${active.binding.quiet_hours.end}.`
            : "."}{" "}
          Just say here if you want it changed.
        </p>
      </section>
    );
  }
  const start = async () => {
    if (busy) return;
    setBusy(true);
    setProblemText(null);
    try {
      if (lookPending && look) {
        await result(
          api.POST("/heinzel/profile", {
            body: {
              session_id: sessionId,
              request_id: `look-${look.request_id.slice(5, 69)}`,
              expected_revision: look.expected_revision,
              name: look.name,
              appearance: look.appearance,
            },
          }),
        );
      }
      if (termsPending && terms) {
        const chat = await result(
          api.POST("/system/runner/chat/propose-agent-contract", {
            params: { query: { durable_session: sessionId, request_id: terms.request_id } },
          }),
        );
        await confirmStaged(chat, "accept-agent-contract");
      }
      if (sourcePending && sourceReview) {
        const chat = await result(
          api.POST("/system/runner/chat/propose-connected-read-review", {
            params: { query: { durable_session: sessionId, request_id: sourceReview.request_id } },
          }),
        );
        await confirmStaged(chat, "approve-connected-read");
      }
      if (schedulePending && terms && schedule && interval && requestId) {
        if (active)
          await result(
            api.POST("/heinzel/pause-agent-schedule", {
              body: { session_id: sessionId, request_id: active.binding.request_id },
            }),
          );
        await result(
          api.POST("/heinzel/propose-agent-schedule", {
            body: {
              session_id: sessionId,
              request_id: requestId,
              contract_request_id: terms.request_id,
              cadence_seconds: interval.cadence_seconds,
              max_requests: MAX_REQUESTS,
              timeout_seconds: Math.min(terms.timeout_seconds, MAX_TIMEOUT),
              connected_request_id: sourceReview?.request_id ?? undefined,
              notification_policy: schedule.notices
                ? {
                    ...notificationPolicy(
                      schedule.notices.max_per_24h,
                      60,
                      noticeMode(schedule.notices.deduplicate_by, sourceReview),
                    ),
                    channels: schedule.notices.channels ?? [],
                  }
                : undefined,
              quiet_hours: schedule.quiet_hours ?? undefined,
              wake_on_new_items: schedule.wake_on_new_items ?? undefined,
            },
          }),
        );
        const chat = await result(
          api.POST("/system/runner/chat/propose-agent-schedule-review", {
            params: { query: { durable_session: sessionId, request_id: requestId } },
          }),
        );
        await confirmStaged(chat, "approve-agent-schedule");
      }
    } catch (reason) {
      setProblemText(reason instanceof Error ? reason.message : "This could not be started.");
    } finally {
      setBusy(false);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["heinzel", "agent-setup", sessionId] }),
        client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] }),
        client.invalidateQueries({ queryKey: ["heinzel", "sessions"] }),
      ]);
    }
  };
  return (
    <section
      aria-label="Review what this helper would do"
      className="space-y-2 rounded-lg border border-border p-3 text-sm"
    >
      <h3 className="font-medium">{running || accepted ? "Review this change" : "Review and start"}</h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {lookPending && look && (
          <>
            <dt className="text-muted-foreground">Name</dt>
            <dd className="flex items-center gap-2">
              <HeinzelAvatar
                {...look.appearance}
                beard={look.appearance.beard ?? undefined}
                hat={look.appearance.hat ?? undefined}
                shirt={look.appearance.shirt ?? undefined}
                className="h-16 w-11"
              />
              {look.name}
            </dd>
          </>
        )}
        {termsPending && terms && (
          <>
            <dt className="text-muted-foreground">Promise</dt>
            <dd className="whitespace-pre-wrap break-words">{terms.contract}</dd>
          </>
        )}
        {source && (
          <>
            <dt className="text-muted-foreground">Reads</dt>
            <dd>
              {source.capability}, read-only
              {sourceReview?.review.account_display ? ` from ${sourceReview.review.account_display}` : ""}
              {source.window
                ? `, only ${windowWords(source.window.lookback_seconds)} back and ${windowWords(source.window.lookahead_seconds)} ahead of each check`
                : ""}
              . Text it reads is treated as data, never as instructions.
            </dd>
          </>
        )}
        {schedulePending && interval && (
          <>
            <dt className="text-muted-foreground">When</dt>
            <dd>
              {schedule?.wake_on_new_items
                ? `Only when something new arrives, checked ${cadenceWords(interval.cadence_seconds).toLowerCase()}`
                : cadenceWords(interval.cadence_seconds)}
              {schedule?.quiet_hours &&
                `, quiet ${schedule.quiet_hours.start} to ${schedule.quiet_hours.end} (${schedule.quiet_hours.timezone})`}
            </dd>
            <dt className="text-muted-foreground">May do</dt>
            <dd>
              {schedule?.notices
                ? `Notify you on this device${
                    schedule.notices.channels?.length ? ` and on ${schedule.notices.channels.join(", ")}` : ""
                  }, at most ${schedule.notices.max_per_24h} a day. Nothing else.`
                : "Write in this chat. Nothing else."}
            </dd>
            <dt className="text-muted-foreground">Cost</dt>
            <dd>Uses your subscription; cost unknown.</dd>
          </>
        )}
      </dl>
      {sourceProblem && (
        <p role="alert" className="text-bad">
          This source isn't ready: {sourceProblem}
        </p>
      )}
      {problemText && (
        <p role="alert" className="text-bad">
          {problemText}
        </p>
      )}
      <Button type="button" size="sm" disabled={busy || (sourcePending && !sourceReview)} onClick={() => void start()}>
        {busy ? "Starting…" : running || accepted ? "Apply" : "Start"}
      </Button>
      <p className="text-xs text-muted-foreground">Not quite right? Say what to change, and I will propose it again.</p>
    </section>
  );
}
