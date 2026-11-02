import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { chatKey } from "@/api/chat";
import {
  api,
  problem,
  type SchemaChatSession,
  type SchemaConnectedGrantProposal,
  type SchemaRollingConnectedGrantProposal,
} from "@/api/client";
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

/** Inspect a known private proposal; no provider or grant is inferred from its absence. */
export function ConnectedReadReview({ sessionId }: { sessionId: string }) {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [selection, setSelection] = useState("");
  const [argumentsText, setArgumentsText] = useState("{}");
  const [rolling, setRolling] = useState(false);
  const [lookback, setLookback] = useState("");
  const [lookahead, setLookahead] = useState("");
  const [timezone, setTimezone] = useState("");
  const [pendingProposal, setPendingProposal] = useState<
    | {
        request_id: string;
        suggestion_request: string;
        suggestion_revision: number;
      }
    | {
        request_id: string;
        capability: string;
        method: string;
        fixed_arguments: Record<string, never>;
        window_policy?: { parameter: string; lookback_seconds: number; lookahead_seconds: number; timezone: string };
      }
    | null
  >(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(`hz.connected-proposal:${sessionId}`) ?? "null");
      return saved &&
        typeof saved.request_id === "string" &&
        ((typeof saved.suggestion_request === "string" &&
          Number.isInteger(saved.suggestion_revision) &&
          saved.suggestion_revision > 0) ||
          (typeof saved.capability === "string" &&
            typeof saved.method === "string" &&
            saved.fixed_arguments &&
            typeof saved.fixed_arguments === "object" &&
            !Array.isArray(saved.fixed_arguments)))
        ? saved
        : null;
    } catch {
      return null;
    }
  });
  const state = useQuery({
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
  const options = useQuery({
    queryKey: ["heinzel", "connected-read-options", sessionId],
    queryFn: () => result(api.GET("/heinzel/connected-read-options", { params: { query: { session_id: sessionId } } })),
    retry: false,
  });
  const suggestion = useQuery({
    queryKey: ["heinzel", "latest-source-suggestion", sessionId],
    queryFn: () =>
      result(api.GET("/heinzel/latest-source-suggestion-view", { params: { query: { session_id: sessionId } } })),
    retry: false,
  });
  const requestId = state.data?.proposal?.request_id ?? null;
  const chatId = params.get("connected-chat");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [revoked, setRevoked] = useState(false);
  const key = ["heinzel", "connected-read-review", sessionId, requestId] as const;
  const review = useQuery({
    queryKey: key,
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/connected-read-review", {
          params: { query: { session_id: sessionId, request_id: requestId ?? "" } },
        }),
      );
      if (value.request_id !== requestId || value.review.session !== sessionId) throw new Error("Unexpected proposal");
      return value;
    },
    enabled: !!requestId,
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
  const proposal = review.data;
  const displayedProposal = proposal ?? state.data?.proposal;
  const ownedChat = chat.data?.id === chatId && chat.data.personal_session_id === sessionId ? chat.data : undefined;
  const action = ownedChat?.actions?.find(
    (item) =>
      item.module === "heinzel" &&
      item.operation === "approve-connected-read" &&
      item.arguments?.session_id === sessionId &&
      item.arguments?.request_id === requestId &&
      !Object.hasOwn(item.arguments ?? {}, "approval_proof") &&
      canonical(item.arguments?.review) === canonical(proposal),
  );
  const remember = (value: SchemaChatSession, expected?: string) => {
    if (value.personal_session_id !== sessionId || (expected && value.id !== expected))
      throw new Error("Unexpected conversation");
    client.setQueryData(chatKey(value.id), value);
  };
  const submit = async (suggested = false) => {
    if (busy) return;
    setBusy(true);
    let request = pendingProposal;
    if (suggested && !request) {
      try {
        const shown = suggestion.data;
        const fresh = await result(
          api.GET("/heinzel/latest-source-suggestion-view", { params: { query: { session_id: sessionId } } }),
        );
        if (
          !shown ||
          !fresh ||
          shown.session_id !== sessionId ||
          canonical(shown) !== canonical(fresh) ||
          !options.data?.some((item) => item.capability === shown.capability && item.method === shown.method)
        )
          throw new Error("The source suggestion changed or is unavailable");
        request = {
          request_id: crypto.randomUUID(),
          suggestion_request: shown.request_id,
          suggestion_revision: shown.revision,
        };
        sessionStorage.setItem(`hz.connected-proposal:${sessionId}`, JSON.stringify(request));
        setPendingProposal(request);
      } catch {
        setError(true);
        setBusy(false);
        return;
      }
    }
    if (!request) {
      const option = options.data?.find((item) => `${item.capability}.${item.method}` === selection);
      if (!option) {
        setBusy(false);
        return;
      }
      try {
        const fixed: unknown = JSON.parse(argumentsText);
        if (!fixed || typeof fixed !== "object" || Array.isArray(fixed)) throw new Error("Expected object");
        request = {
          request_id: crypto.randomUUID(),
          capability: option.capability,
          method: option.method,
          fixed_arguments: fixed as Record<string, never>,
        };
        if (rolling) {
          if (!option.window_parameter || !lookback.trim() || !lookahead.trim() || !timezone.trim())
            throw new Error("Complete the rolling window terms");
          const before = Number(lookback),
            after = Number(lookahead);
          if (
            !Number.isInteger(before) ||
            !Number.isInteger(after) ||
            before < 0 ||
            after < 0 ||
            before + after < 1 ||
            before + after > 31 * 86400
          )
            throw new Error("Invalid rolling window");
          request.window_policy = {
            parameter: option.window_parameter,
            lookback_seconds: before,
            lookahead_seconds: after,
            timezone: timezone.trim(),
          };
        }
        // Persist before sending so a lost response can be retried with the exact same request.
        sessionStorage.setItem(`hz.connected-proposal:${sessionId}`, JSON.stringify(request));
        setPendingProposal(request);
      } catch {
        setError(true);
        setBusy(false);
        return;
      }
    }
    setError(false);
    try {
      if ("suggestion_request" in request) {
        await result(api.POST("/heinzel/prepare-suggested-source", { body: { ...request, session_id: sessionId } }));
      } else if (request.window_policy) {
        await result(
          api.POST("/heinzel/propose-rolling-connected-read", {
            body: { ...request, window_policy: request.window_policy, session_id: sessionId },
          }),
        );
      } else {
        await result(api.POST("/heinzel/propose-connected-read", { body: { ...request, session_id: sessionId } }));
      }
      await client.invalidateQueries({ queryKey: ["heinzel", "connected-read-state", sessionId] });
      sessionStorage.removeItem(`hz.connected-proposal:${sessionId}`);
      setPendingProposal(null);
      setParams(
        (old) => {
          const next = new URLSearchParams(old);
          next.delete("connected-chat");
          return next;
        },
        { replace: true },
      );
    } catch {
      setError(true);
      await client.invalidateQueries({ queryKey: ["heinzel", "connected-read-state", sessionId] });
    } finally {
      setBusy(false);
    }
  };
  const stage = async () => {
    if (!proposal || busy) return;
    setBusy(true);
    setError(false);
    try {
      const value = await result(
        api.POST("/system/runner/chat/propose-connected-read-review", {
          params: { query: { durable_session: sessionId, request_id: proposal.request_id } },
        }),
      );
      remember(value);
      setParams(
        (old) => {
          const next = new URLSearchParams(old);
          next.set("connected-chat", value.id);
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
    if (!ownedChat || !action || !proposal || busy) return;
    setBusy(true);
    setError(false);
    try {
      if (confirm) {
        const current = await result(
          api.GET("/heinzel/connected-read-review", {
            params: { query: { session_id: sessionId, request_id: proposal.request_id } },
          }),
        );
        if (current.review.session !== sessionId || canonical(current) !== canonical(proposal))
          throw new Error("Terms changed");
      }
      const value = await result(
        confirm
          ? api.POST("/system/runner/chat/confirm", { params: { query: { session: ownedChat.id, action: action.id } } })
          : api.POST("/system/runner/chat/decline", {
              params: { query: { session: ownedChat.id, action: action.id } },
            }),
      );
      remember(value, ownedChat.id);
      await client.invalidateQueries({ queryKey: ["heinzel", "connected-read-state", sessionId] });
    } catch {
      setError(true);
      try {
        remember(
          await result(api.GET("/system/runner/chat/show", { params: { query: { session: ownedChat.id } } })),
          ownedChat.id,
        );
      } catch {
        /* Never repeat an uncertain confirmation. */
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
        api.POST("/system/runner/chat/recover", { params: { query: { session: ownedChat.id, action: action.id } } }),
      );
      remember(value, ownedChat.id);
      await client.invalidateQueries({ queryKey: ["heinzel", "connected-read-state", sessionId] });
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  const revoke = async () => {
    if (!proposal || busy) return;
    setBusy(true);
    setError(false);
    try {
      await result(
        api.POST("/heinzel/revoke-connected-read", {
          body: { session_id: sessionId, request_id: proposal.request_id },
        }),
      );
      setRevoked(true);
      await client.invalidateQueries({ queryKey: ["heinzel", "connected-read-state", sessionId] });
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-label="Connected read review" className="space-y-2 rounded border border-border p-3 text-sm">
      <h3 className="font-medium">Connected read review</h3>
      <p>
        Reads use exact reviewed arguments and remain approved until revoked or stale. Account and source are rechecked
        before each read.
      </p>
      {suggestion.data && suggestion.data.session_id === sessionId && (
        <div className="space-y-1 rounded border p-2">
          <p>
            Suggested source · version {suggestion.data.revision}. This suggestion has not contacted a provider or
            granted access.
          </p>
          <p>
            {suggestion.data.capability}.{suggestion.data.method}
          </p>
          <pre className="overflow-x-auto whitespace-pre-wrap break-all">
            {JSON.stringify(suggestion.data.fixed_arguments, null, 2)}
          </pre>
          <Button
            type="button"
            size="sm"
            disabled={
              busy ||
              !!pendingProposal ||
              !options.data?.some(
                (item) => item.capability === suggestion.data?.capability && item.method === suggestion.data?.method,
              )
            }
            onClick={() => void submit(true)}
          >
            Review source
          </Button>
        </div>
      )}
      {options.isError || state.isError ? <p role="alert">Connected read availability could not be verified.</p> : null}
      {options.data?.length === 0 && <p>No selected provider exposes a connected read for review.</p>}
      {!!options.data?.length && (
        <div className="space-y-2">
          <label className="block">
            Read operation{" "}
            <select
              className="w-full rounded border p-1"
              value={selection}
              disabled={busy || !!pendingProposal}
              onChange={(event) => setSelection(event.target.value)}
            >
              <option value="">Choose an operation</option>
              {options.data.map((item) => (
                <option key={`${item.capability}.${item.method}`} value={`${item.capability}.${item.method}`}>
                  {item.capability}.{item.method} · {item.provider}
                </option>
              ))}
            </select>
          </label>
          {options.data.find((item) => `${item.capability}.${item.method}` === selection) && (
            <details>
              <summary>Argument schema and source</summary>
              <pre className="overflow-x-auto whitespace-pre-wrap break-all">
                {JSON.stringify(
                  options.data.find((item) => `${item.capability}.${item.method}` === selection),
                  null,
                  2,
                )}
              </pre>
            </details>
          )}
          {options.data.find((item) => `${item.capability}.${item.method}` === selection)?.window_parameter && (
            <div className="space-y-2">
              <label>
                <input
                  type="checkbox"
                  checked={rolling}
                  disabled={busy || !!pendingProposal}
                  onChange={(event) => setRolling(event.target.checked)}
                />{" "}
                Use a rolling window instead of a fixed window
              </label>
              {rolling && (
                <div className="space-y-2">
                  <p>
                    Specify elapsed seconds around the host clock. The selected timezone is for display; bounds are
                    computed in UTC. Omit the window from fixed arguments.
                  </p>
                  <label className="block">
                    Lookback seconds{" "}
                    <input
                      className="rounded border p-1"
                      type="number"
                      min="0"
                      value={lookback}
                      disabled={busy || !!pendingProposal}
                      onChange={(event) => setLookback(event.target.value)}
                    />
                  </label>
                  <label className="block">
                    Lookahead seconds{" "}
                    <input
                      className="rounded border p-1"
                      type="number"
                      min="0"
                      value={lookahead}
                      disabled={busy || !!pendingProposal}
                      onChange={(event) => setLookahead(event.target.value)}
                    />
                  </label>
                  <label className="block">
                    Display timezone (IANA){" "}
                    <input
                      className="rounded border p-1"
                      value={timezone}
                      disabled={busy || !!pendingProposal}
                      onChange={(event) => setTimezone(event.target.value)}
                    />
                  </label>
                </div>
              )}
            </div>
          )}
          <label className="block">
            Fixed arguments (JSON)
            <textarea
              className="w-full rounded border p-1"
              value={argumentsText}
              disabled={busy || !!pendingProposal}
              onChange={(event) => setArgumentsText(event.target.value)}
            />
          </label>
          <Button
            type="button"
            size="sm"
            disabled={busy || (!selection && !pendingProposal)}
            onClick={() => void submit()}
          >
            {pendingProposal ? "Retry the same proposal" : "Prepare review"}
          </Button>
          {pendingProposal && (
            <div>
              <p>Retained for an exact retry. Check the recorded proposal if the response was lost.</p>
              <Button
                type="button"
                size="sm"
                disabled={busy}
                onClick={() => {
                  sessionStorage.removeItem(`hz.connected-proposal:${sessionId}`);
                  setPendingProposal(null);
                }}
              >
                Forget this retry
              </Button>
              <p>Forgetting this retry does not remove a proposal that may already have been recorded.</p>
            </div>
          )}
        </div>
      )}
      {state.data?.status !== "none" && state.data && (
        <p>Recorded state: {state.data.status}. A recorded approval is checked again before use.</p>
      )}
      {review.isError && <p role="alert">The proposal or selected provider could not be verified.</p>}
      {chatId && chat.isError && <p role="alert">The saved review conversation could not be verified.</p>}
      {error && <p role="alert">The action could not be verified. Inspect the saved outcome before deciding again.</p>}
      {displayedProposal && <Terms proposal={displayedProposal} />}
      {state.data?.descriptor && (
        <details>
          <summary>Selected provider and read schema</summary>
          <pre className="overflow-x-auto whitespace-pre-wrap break-all">
            {JSON.stringify(state.data.descriptor, null, 2)}
          </pre>
        </details>
      )}
      {proposal && state.data?.status === "proposed" && !chatId && (
        <Button type="button" size="sm" disabled={busy} onClick={() => void stage()}>
          Review access
        </Button>
      )}
      {action?.status === "pending" && (
        <ol>
          <ActionCard
            action={action}
            busy={busy}
            proposalTitle="Review connected read"
            onDecide={(confirm) => void decide(confirm)}
          />
        </ol>
      )}
      {action?.status === "done" && (
        <p role="status">This review was confirmed. Current grant status is not available here.</p>
      )}
      {action?.status === "declined" && <p>This review was declined.</p>}
      {(action?.status === "running" || action?.status === "uncertain") && (
        <div role="alert">
          <p>The outcome needs inspection; confirmation will not be retried. Select Check outcome to look again.</p>
          <Button type="button" size="sm" disabled={busy} onClick={() => void recover()}>
            Check outcome
          </Button>
        </div>
      )}
      {state.data?.status === "approved" && !revoked && proposal && (
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void revoke()}>
          Revoke this grant
        </Button>
      )}
      {revoked && <p role="status">Revocation recorded for this grant.</p>}
    </section>
  );
}

function Terms({ proposal }: { proposal: SchemaConnectedGrantProposal | SchemaRollingConnectedGrantProposal }) {
  const terms = proposal.review;
  return (
    <div className="space-y-1 break-words">
      <p>
        Proposed revision {proposal.revision}: {proposal.capability}.{proposal.method}
      </p>
      <p>Account: {terms.account_display || terms.account_identity}</p>
      <p>Provider-reported account identity: {terms.account_identity}</p>
      <p>Provider-reported source revision: {terms.source_revision}</p>
      <p>Operation: {terms.operation}</p>
      <p>Provider-reported scopes: {terms.provider_scopes.join(", ") || "none"}</p>
      {terms.item_identity && (
        <p className="break-words">
          Provider-reported item identity: collection {terms.item_identity.collection.join(".") || "result array"},
          identity field {terms.item_identity.identity_field}
          {terms.item_identity.revision_field ? `, revision field ${terms.item_identity.revision_field}` : ""}. These
          claims do not guarantee complete source history.
        </p>
      )}
      {terms.candidate_read_disclosure && (
        <p className="break-words">Provider-reported candidate read: {terms.candidate_read_disclosure}</p>
      )}
      {"window_policy" in proposal && (
        <p>
          Rolling window: {proposal.window_policy.lookback_seconds} seconds before and{" "}
          {proposal.window_policy.lookahead_seconds} seconds after the host clock, display timezone{" "}
          {proposal.window_policy.timezone}. The window below is the review-time example; each read calculates new UTC
          bounds.
        </p>
      )}
      <p>Reviewed arguments{"window_policy" in proposal ? " and example window" : ""}:</p>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all">
        {JSON.stringify(terms.fixed_arguments, null, 2)}
      </pre>
      <p>
        The schema is bound by digest {terms.schema_sha256}; its description is not available here. The grant remains
        valid until revoked or stale. Confirmation does not perform the requested read.
      </p>
    </div>
  );
}
