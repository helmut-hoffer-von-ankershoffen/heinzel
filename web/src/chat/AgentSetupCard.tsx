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

async function confirmStaged(
  chat: { id: string; actions?: { id: string; operation: string; status?: string }[] | null },
  operation: string,
) {
  const action = chat.actions?.find((item) => item.operation === operation && item.status === "pending");
  if (!action) throw new Error("There is nothing to confirm");
  const updated = await result(
    api.POST("/system/runner/chat/confirm", { params: { query: { session: chat.id, action: action.id } } }),
  );
  if (updated.actions?.find((item) => item.id === action.id)?.status !== "done") throw new Error("Not confirmed");
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
      const [terms, schedule, look, schedules] = await Promise.all([
        result(api.GET("/heinzel/latest-scheduled-agent-proposal", query)),
        result(api.GET("/heinzel/latest-schedule-suggestion-view", query)),
        result(api.GET("/heinzel/latest-profile-proposal", query)),
        result(
          api.GET("/heinzel/agent-schedules", { params: { query: { session_id: sessionId, cursor: 0, limit: 100 } } }),
        ),
      ]);
      const review = terms
        ? await result(
            api.GET("/heinzel/agent-contract-review", {
              params: { query: { session_id: sessionId, request_id: terms.request_id } },
            }),
          )
        : null;
      return { terms, schedule, look, schedules: schedules.items, accepted: review?.accepted ?? false };
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
  const { terms, schedule, look, schedules, accepted } = data;
  const interval = schedule && "kind" in schedule.trigger ? schedule.trigger : null;
  const requestId = terms && schedule ? `setup-${schedule.request_id.slice(5, 37)}-${terms.revision}` : null;
  const active = schedules.find((item) => item.status === "approved");
  const running = !!active && active.binding.request_id === requestId;
  const lookPending = !!look && look.expected_revision === (snapshot.profile_revision ?? 0) && look.name !== undefined;
  const termsPending = !!terms && !accepted;
  const schedulePending = !!interval && !!terms && !running;
  if (!termsPending && !schedulePending && !lookPending) {
    if (!active) return null;
    return (
      <section aria-label="Running" className="rounded-lg border border-border p-3 text-sm">
        <p>
          <strong>{snapshot.name}</strong> is running. {cadenceWords(active.binding.cadence_seconds)}
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
              notification_policy: schedule.notices
                ? notificationPolicy(schedule.notices.max_per_24h, 60, schedule.notices.deduplicate_by ?? "topic")
                : undefined,
              quiet_hours: schedule.quiet_hours ?? undefined,
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
        {schedulePending && interval && (
          <>
            <dt className="text-muted-foreground">When</dt>
            <dd>
              {cadenceWords(interval.cadence_seconds)}
              {schedule?.quiet_hours &&
                `, quiet ${schedule.quiet_hours.start} to ${schedule.quiet_hours.end} (${schedule.quiet_hours.timezone})`}
            </dd>
            <dt className="text-muted-foreground">May do</dt>
            <dd>
              {schedule?.notices
                ? `Notify you on this device, at most ${schedule.notices.max_per_24h} a day. Nothing else.`
                : "Write in this chat. Nothing else."}
            </dd>
            <dt className="text-muted-foreground">Cost</dt>
            <dd>Uses your subscription; cost unknown.</dd>
          </>
        )}
      </dl>
      {problemText && (
        <p role="alert" className="text-bad">
          {problemText}
        </p>
      )}
      <Button type="button" size="sm" disabled={busy} onClick={() => void start()}>
        {busy ? "Starting…" : running || accepted ? "Apply" : "Start"}
      </Button>
      <p className="text-xs text-muted-foreground">Not quite right? Say what to change, and I will propose it again.</p>
    </section>
  );
}
