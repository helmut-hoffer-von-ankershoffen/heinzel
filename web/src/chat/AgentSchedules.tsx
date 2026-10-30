import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, problem } from "@/api/client";
import type { components } from "@/api/schema";
import { AgentScheduleReview } from "@/chat/AgentScheduleReview";
import { ScheduledNotificationAttempts } from "@/chat/ScheduledNotificationAttempts";
import {
  notificationPolicy,
  ScheduledNotificationConsent,
  sameNotificationPolicy,
  validNotificationPolicy,
} from "@/chat/ScheduledNotificationConsent";
import { Button } from "@/components/ui/button";

async function result<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok && data !== undefined) return data;
  throw problem(response.status, error);
}

type Draft = {
  session_id: string;
  request_id: string;
  contract_request_id: string;
  cadence_seconds: number;
  max_requests: number;
  timeout_seconds: number;
  connected_request_id?: string | null;
  notification_policy?: components["schemas"]["ScheduledNotificationPolicy"] | null;
};

/** Separately propose recurring turns; no schedule is approved by submitting this form. */
export function AgentSchedules({ sessionId }: { sessionId: string }) {
  const client = useQueryClient();
  const key = `hz.agent-schedule.proposal.${sessionId}`;
  const [cursor, setCursor] = useState(0);
  const [minutes, setMinutes] = useState(5);
  const [requests, setRequests] = useState(1);
  const [seconds, setSeconds] = useState(30);
  const [includeConnected, setIncludeConnected] = useState(false);
  const [includeNotifications, setIncludeNotifications] = useState(false);
  const [noticeMax, setNoticeMax] = useState(3);
  const [noticeCooldown, setNoticeCooldown] = useState(3600);
  const suggestion = useQuery({
    queryKey: ["heinzel", "latest-schedule-suggestion", sessionId],
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/latest-schedule-suggestion-view", { params: { query: { session_id: sessionId } } }),
      );
      if (value) {
        if (value.session_id !== sessionId || value.status !== "suggested") throw new Error("Unexpected suggestion");
        if (
          "kind" in value.trigger &&
          (value.trigger.kind !== "interval" ||
            !Number.isInteger(value.trigger.cadence_seconds) ||
            value.trigger.cadence_seconds < 60 ||
            value.trigger.cadence_seconds > 86400 ||
            value.trigger.cadence_seconds % 60 !== 0)
        )
          throw new Error("Unexpected interval");
      }
      return value;
    },
    retry: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [saved] = useState((): { pending: Draft | null; invalid: boolean } => {
    try {
      const stored = sessionStorage.getItem(key);
      if (!stored) return { pending: null, invalid: false };
      const value = JSON.parse(stored) as Draft;
      if (
        value.session_id !== sessionId ||
        typeof value.request_id !== "string" ||
        !value.request_id ||
        typeof value.contract_request_id !== "string" ||
        !value.contract_request_id ||
        !Number.isInteger(value.cadence_seconds) ||
        value.cadence_seconds < 60 ||
        value.cadence_seconds > 86400 ||
        value.cadence_seconds % 60 !== 0 ||
        !Number.isInteger(value.max_requests) ||
        value.max_requests < 1 ||
        value.max_requests > 4 ||
        !Number.isInteger(value.timeout_seconds) ||
        value.timeout_seconds < 1 ||
        value.timeout_seconds > 120 ||
        (value.notification_policy != null && !validNotificationPolicy(value.notification_policy))
      )
        throw new Error("Invalid saved schedule proposal");
      return { pending: value, invalid: false };
    } catch {
      return { pending: null, invalid: true };
    }
  });
  const [pending, setPending] = useState(saved.pending);
  const proposal = useQuery({
    queryKey: ["heinzel", "agent-proposal", sessionId],
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/latest-scheduled-agent-proposal", { params: { query: { session_id: sessionId } } }),
      );
      if (value && value.session_id !== sessionId) throw new Error("Unexpected session");
      return value;
    },
    retry: false,
  });
  const contract = useQuery({
    queryKey: ["heinzel", "agent-schedule-contract-review", sessionId, proposal.data?.request_id],
    queryFn: async () => {
      if (!proposal.data) throw new Error("No contract");
      const value = await result(
        api.GET("/heinzel/agent-contract-review", {
          params: { query: { session_id: sessionId, request_id: proposal.data.request_id } },
        }),
      );
      if (
        value.proposal.session_id !== sessionId ||
        value.proposal.preview_sha256 !== proposal.data.preview_sha256 ||
        value.proposal.request_id !== proposal.data.request_id
      )
        throw new Error("Unexpected contract");
      return value;
    },
    enabled: !!proposal.data,
    retry: false,
  });
  const connected = useQuery({
    queryKey: ["heinzel", "connected-read-state", sessionId],
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/connected-read-state", { params: { query: { session_id: sessionId } } }),
      );
      if (value.session_id !== sessionId || (value.proposal && value.proposal.review.session !== sessionId))
        throw new Error("Unexpected session");
      return value;
    },
    retry: false,
  });
  const schedules = useQuery({
    queryKey: ["heinzel", "agent-schedules", sessionId, cursor],
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/agent-schedules", { params: { query: { session_id: sessionId, cursor, limit: 20 } } }),
      );
      if (value.session_id !== sessionId || value.items.some((item) => item.binding.session_id !== sessionId))
        throw new Error("Unexpected session");
      if (value.next_cursor != null && value.next_cursor <= cursor) throw new Error("Invalid cursor");
      return value;
    },
    retry: false,
  });
  const propose = async () => {
    if (busy || saved.invalid || (!pending && (!proposal.data || !contract.data?.accepted))) return;
    if (!pending && !proposal.data) return;
    const request = pending ?? {
      session_id: sessionId,
      request_id: crypto.randomUUID(),
      contract_request_id: proposal.data?.request_id ?? "",
      cadence_seconds: minutes * 60,
      max_requests: requests,
      timeout_seconds: seconds,
      connected_request_id:
        includeConnected && connected.data?.status === "approved" ? connected.data.proposal?.request_id : undefined,
      notification_policy: includeNotifications ? notificationPolicy(noticeMax, noticeCooldown) : undefined,
    };
    setBusy(true);
    setError(false);
    try {
      const encoded = JSON.stringify(request);
      sessionStorage.setItem(key, encoded);
      if (sessionStorage.getItem(key) !== encoded) throw new Error("Cannot save request");
      setPending(request);
      const value = await result(api.POST("/heinzel/propose-agent-schedule", { body: request }));
      if (
        value.session_id !== sessionId ||
        value.request_id !== request.request_id ||
        value.contract_request_id !== request.contract_request_id ||
        value.cadence_seconds !== request.cadence_seconds ||
        value.max_requests !== request.max_requests ||
        value.timeout_seconds !== request.timeout_seconds ||
        (value.connected_grant?.request_id ?? null) !== (request.connected_request_id ?? null) ||
        !sameNotificationPolicy(value.notification_policy, request.notification_policy)
      )
        throw new Error("Unexpected proposal");
      sessionStorage.removeItem(key);
      setPending(null);
      setCursor(0);
      await client.invalidateQueries({ queryKey: ["heinzel", "agent-schedules", sessionId] });
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  const pause = async (requestId: string) => {
    if (busy) return;
    setBusy(true);
    setError(false);
    try {
      const value = await result(
        api.POST("/heinzel/pause-agent-schedule", { body: { session_id: sessionId, request_id: requestId } }),
      );
      if (value.binding.session_id !== sessionId || value.binding.request_id !== requestId || value.status !== "paused")
        throw new Error("Unexpected schedule");
    } catch {
      setError(true);
    } finally {
      await client.invalidateQueries({ queryKey: ["heinzel", "agent-schedules", sessionId] });
      setBusy(false);
    }
  };
  return (
    <>
      <details className="rounded border border-border p-3 text-sm">
        <summary className="cursor-pointer font-medium">Recurring agent turns</summary>
        <div className="space-y-3 pt-2">
          <p>
            Set a cadence for this agent’s accepted terms. Approval is separate. This path uses the selected Claude
            subscription and has unknown cost. Connected reads require separate approval and are included only when
            selected below.
          </p>
          <section className="space-y-2 rounded border border-border p-2" aria-label="Chat schedule suggestion">
            <p className="font-medium">Chat schedule suggestion</p>
            <Button type="button" size="sm" variant="outline" onClick={() => void suggestion.refetch()}>
              Refresh suggestion
            </Button>
            {suggestion.isError && <p role="alert">The suggestion could not be verified.</p>}
            {suggestion.data === null && <p>No schedule suggested yet.</p>}
            {suggestion.data && (
              <>
                <p>Suggestion revision {suggestion.data.revision}; not approved or active.</p>
                {"kind" in suggestion.data.trigger ? (
                  <>
                    <p>Suggested interval: every {suggestion.data.trigger.cadence_seconds / 60} minutes.</p>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={!!pending || busy || !Number.isInteger(suggestion.data.trigger.cadence_seconds / 60)}
                      onClick={() => {
                        if (suggestion.data && "kind" in suggestion.data.trigger)
                          setMinutes(suggestion.data.trigger.cadence_seconds / 60);
                      }}
                    >
                      Use interval in preview
                    </Button>
                  </>
                ) : (
                  <p>
                    Suggested calendar schedule: {suggestion.data.trigger.cron} ({suggestion.data.trigger.timezone}).
                    Calendar schedules are not supported here; this suggestion remains inactive.
                  </p>
                )}
                {suggestion.data.suggested_reads.length > 0 && (
                  <p className="break-words">
                    Suggested reads:{" "}
                    {suggestion.data.suggested_reads.map((read) => `${read.capability}.${read.method}`).join(", ")}.
                    These do not grant access. Choose an already approved read separately.
                  </p>
                )}
              </>
            )}
          </section>
          {!contract.data?.accepted && <p>Accept the current agent terms before proposing recurring turns.</p>}
          {connected.data?.status === "approved" && connected.data.proposal && (
            <label className="block">
              <input
                type="checkbox"
                checked={pending ? !!pending.connected_request_id : includeConnected}
                disabled={!!pending || busy}
                onChange={(event) => setIncludeConnected(event.target.checked)}
              />{" "}
              Allow the separately approved {connected.data.proposal.capability}.{connected.data.proposal.method} read
              during these scheduled turns. The schedule will stop if its exact account, source, arguments or grant
              changes.
            </label>
          )}
          {connected.isError && (
            <p role="alert">Connected read status could not be verified; scheduled reads are unavailable.</p>
          )}
          <ScheduledNotificationConsent
            enabled={pending ? !!pending.notification_policy : includeNotifications}
            maxPer24h={pending?.notification_policy?.max_per_24h ?? noticeMax}
            cooldown={pending?.notification_policy?.topic_cooldown_seconds ?? noticeCooldown}
            disabled={!!pending || busy}
            onEnabled={setIncludeNotifications}
            onMax={setNoticeMax}
            onCooldown={setNoticeCooldown}
          />
          <div className="flex flex-wrap items-end gap-2">
            <label>
              Every (minutes)
              <input
                className="block w-24 rounded border p-1"
                type="number"
                min={1}
                max={1440}
                value={pending ? pending.cadence_seconds / 60 : minutes}
                disabled={!!pending || busy}
                onChange={(e) => setMinutes(Number(e.target.value))}
              />
            </label>
            <label>
              Requests per turn
              <input
                className="block w-24 rounded border p-1"
                type="number"
                min={1}
                max={4}
                value={pending?.max_requests ?? requests}
                disabled={!!pending || busy}
                onChange={(e) => setRequests(Number(e.target.value))}
              />
            </label>
            <label>
              Seconds per turn
              <input
                className="block w-24 rounded border p-1"
                type="number"
                min={1}
                max={Math.min(120, proposal.data?.timeout_seconds ?? 120)}
                value={pending?.timeout_seconds ?? seconds}
                disabled={!!pending || busy}
                onChange={(e) => setSeconds(Number(e.target.value))}
              />
            </label>
            <Button
              type="button"
              size="sm"
              disabled={
                busy ||
                saved.invalid ||
                (!pending &&
                  (!contract.data?.accepted ||
                    !proposal.data ||
                    proposal.data.max_usd !== null ||
                    (includeNotifications && !validNotificationPolicy(notificationPolicy(noticeMax, noticeCooldown))) ||
                    !Number.isInteger(minutes) ||
                    minutes < 1 ||
                    minutes > 1440 ||
                    !Number.isInteger(requests) ||
                    requests < 1 ||
                    requests > 4 ||
                    !Number.isInteger(seconds) ||
                    seconds < 1 ||
                    seconds > Math.min(120, proposal.data.timeout_seconds)))
              }
              onClick={() => void propose()}
            >
              {pending ? "Retry same proposal" : "Propose recurring turns"}
            </Button>
          </div>
          {saved.invalid && (
            <p role="alert">
              The saved proposal request cannot be read. New proposals are blocked to avoid duplicating it.
            </p>
          )}
          {error && (
            <p role="alert">
              The schedule request could not be confirmed. Inspect the saved schedules or retry the same proposal.
            </p>
          )}
          {schedules.isError && <p role="alert">Saved schedules are unavailable.</p>}
          {schedules.data?.items.map(({ binding, status }) => (
            <section key={binding.request_id} className="space-y-2 rounded border border-border p-2">
              <p>
                Every {binding.cadence_seconds / 60} minutes · {status} · contract revision {binding.contract_revision}
              </p>
              <p>
                Up to {binding.max_requests} model requests and {binding.timeout_seconds} seconds per turn.{" "}
                {binding.connected_grant
                  ? `Includes the approved ${binding.connected_grant.capability}.${binding.connected_grant.method} read.`
                  : "No external tools."}
              </p>
              {binding.notification_policy && (
                <p>
                  Local notifications: up to {binding.notification_policy.max_per_24h} attempts per rolling 24 hours,
                  one per turn, with {binding.notification_policy.topic_cooldown_seconds} seconds between the same
                  model-chosen topic. Queued does not mean shown.
                </p>
              )}
              {status === "proposed" && <AgentScheduleReview sessionId={sessionId} proposal={binding} />}
              {(status === "approved" || status === "stale") && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void pause(binding.request_id)}
                >
                  Pause future turns
                </Button>
              )}
              {status === "approved" && (
                <p>
                  Approved; the daemon must be running. A first turn may run in the current interval after approval.
                </p>
              )}
            </section>
          ))}
          <div className="flex gap-2">
            {cursor > 0 && (
              <Button type="button" size="sm" variant="outline" onClick={() => setCursor(0)}>
                First schedules
              </Button>
            )}
            {schedules.data?.next_cursor != null && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  const next = schedules.data?.next_cursor;
                  if (typeof next === "number") setCursor(next);
                }}
              >
                More schedules
              </Button>
            )}
          </div>
        </div>
      </details>
      <ScheduledNotificationAttempts key={`scheduled-notification:${sessionId}`} sessionId={sessionId} />
    </>
  );
}
