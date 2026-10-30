import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, problem, type SchemaChatAction, type SchemaChatSession, type SchemaReadSetSelection } from "@/api/client";
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

function label(value: string): string {
  const text = value.replace(/[._-]+/g, " ").trim();
  return text ? text[0]?.toUpperCase() + text.slice(1) : "Source";
}

function describe(entry: Pick<SchemaReadSetSelection, "capability" | "method">): string {
  return `${label(entry.capability)} · ${label(entry.method)}`;
}

function readable(value: unknown, depth = 0): string {
  if (depth > 8) return "See exact details";
  if (value === null) return "None";
  if (Array.isArray(value)) return value.map((item) => readable(item, depth + 1)).join(", ") || "None";
  if (typeof value === "object")
    return (
      Object.entries(value)
        .map(([name, item]) => `${label(name)}: ${readable(item, depth + 1)}`)
        .join(" · ") || "None"
    );
  return String(value);
}

function argumentsSummary(args: SchemaReadSetSelection["fixed_arguments"]): string {
  const values = Object.entries(args).map(([name, value]) => `${label(name)}: ${readable(value)}`);
  return values.length ? values.join(" · ") : "All items in this read";
}

function recoveryState(key: string): {
  chat: string | null;
  review: string | null;
  decision: boolean;
  proposal: string | null;
  available: boolean;
} {
  try {
    return {
      chat: sessionStorage.getItem(`${key}:chat`),
      review: sessionStorage.getItem(`${key}:review`),
      decision: sessionStorage.getItem(`${key}:decision`) === "uncertain",
      proposal: sessionStorage.getItem(`${key}:proposal`),
      available: true,
    };
  } catch {
    return { chat: null, review: null, decision: true, proposal: null, available: false };
  }
}

function persist(key: string, value: string): void {
  try {
    sessionStorage.setItem(key, value);
    if (sessionStorage.getItem(key) === value) return;
  } catch {
    // The caller keeps the pending state and does not send a mutation.
  }
  throw new Error("Recovery state could not be saved");
}

function forget(key: string): void {
  try {
    sessionStorage.removeItem(key);
    if (sessionStorage.getItem(key) === null) return;
  } catch {
    // Leave the recovery state fenced if removal cannot be verified.
  }
  throw new Error("Recovery state could not be cleared");
}

/** A human reviews a whole bounded set; neither a suggestion nor a review grants access. */
export function ConnectedReadSetReview({ sessionId, onAsk }: { sessionId: string; onAsk?: () => void }) {
  const context = useQuery({
    queryKey: ["heinzel", "connected-read-set-context", sessionId],
    retry: false,
    refetchOnMount: "always",
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/connected-read-set-context", { params: { query: { session_id: sessionId } } }),
      );
      if (value.session_id !== sessionId || !value.owner) throw new Error("Unexpected owner or session");
      return { session_id: sessionId, owner: value.owner };
    },
  });
  if (context.isFetching || context.isError || !context.data)
    return context.isError ? <p role="alert">Sources could not be loaded.</p> : null;
  return (
    <OwnedReadSetReview
      key={`${sessionId}:${context.data.owner}`}
      sessionId={sessionId}
      owner={context.data.owner}
      onAsk={onAsk}
    />
  );
}

function OwnedReadSetReview({ sessionId, owner, onAsk }: { sessionId: string; owner: string; onAsk?: () => void }) {
  const client = useQueryClient();
  const storageKey = `hz.connected-set:${sessionId}:${owner}`;
  const [recovery] = useState(() => recoveryState(storageKey));
  const [chatId, setChatId] = useState<string | null>(recovery.chat);
  const [reviewId, setReviewId] = useState<string | null>(recovery.review);
  const [reviewReady, setReviewReady] = useState(!!recovery.review);
  const [uncertain, setUncertain] = useState(recovery.decision);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [selection, setSelection] = useState("");
  const [argumentsText, setArgumentsText] = useState("{}");
  const [entries, setEntries] = useState<SchemaReadSetSelection[]>([]);
  const [pending, setPending] = useState<{ request_id: string; selections: SchemaReadSetSelection[] } | null>(() => {
    try {
      const saved = JSON.parse(recovery.proposal ?? "null");
      return saved && typeof saved.request_id === "string" && Array.isArray(saved.selections) ? saved : null;
    } catch {
      return null;
    }
  });
  const key = ["heinzel", "connected-read-set", sessionId, owner] as const;
  const proposal = useQuery({
    queryKey: [...key, "proposal"],
    retry: false,
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/connected-read-set-proposal", { params: { query: { session_id: sessionId } } }),
      );
      if (value && (value.session !== sessionId || value.owner !== owner))
        throw new Error("Unexpected owner or session");
      return value;
    },
  });
  const suggestion = useQuery({
    queryKey: [...key, "suggestion"],
    retry: false,
    queryFn: () =>
      result(api.GET("/heinzel/latest-source-set-suggestion-view", { params: { query: { session_id: sessionId } } })),
  });
  const options = useQuery({
    queryKey: [...key, "options"],
    retry: false,
    queryFn: () => result(api.GET("/heinzel/connected-read-options", { params: { query: { session_id: sessionId } } })),
  });
  const approval = useQuery({
    queryKey: [...key, "approval"],
    retry: false,
    enabled: !!proposal.data,
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/connected-read-set-approval", { params: { query: { session_id: sessionId } } }),
      );
      if (value.proposal.session !== sessionId || canonical(value.proposal) !== canonical(proposal.data))
        throw new Error("Unexpected approval");
      return value;
    },
  });
  const review = useQuery({
    queryKey: [...key, "review", reviewId],
    retry: false,
    enabled: !!reviewId && !!proposal.data && reviewReady,
    queryFn: async () => {
      const value = await result(
        api.GET("/heinzel/connected-read-set-review", {
          params: { query: { session_id: sessionId, request_id: reviewId ?? "" } },
        }),
      );
      if (
        value.request_id !== reviewId ||
        value.proposal.session !== sessionId ||
        canonical(value.proposal) !== canonical(proposal.data)
      )
        throw new Error("Unexpected review");
      return value;
    },
  });
  const chat = useQuery({
    queryKey: [...key, "chat", chatId],
    retry: false,
    enabled: !!chatId,
    queryFn: async () => {
      const value = await result(api.GET("/system/runner/chat/show", { params: { query: { session: chatId ?? "" } } }));
      if (value.id !== chatId || value.personal_session_id !== sessionId || value.personal_owner_id !== owner)
        throw new Error("Unexpected conversation");
      return value;
    },
  });
  const action = chat.data?.actions?.find(
    (item) =>
      item.module === "heinzel" &&
      item.operation === "approve-connected-read-set" &&
      item.arguments?.session_id === sessionId &&
      item.arguments?.request_id === reviewId &&
      !Object.hasOwn(item.arguments ?? {}, "approval_proof") &&
      canonical(item.arguments?.review) === canonical(review.data),
  );
  const refresh = () => client.invalidateQueries({ queryKey: key });
  const run = async (operation: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError(false);
    try {
      if (!recovery.available) throw new Error("Recovery storage is unavailable");
      await operation();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  const propose = (chosen: SchemaReadSetSelection[]) =>
    void run(async () => {
      if (uncertain) throw new Error("The previous decision requires inspection");
      const request = pending ?? { request_id: crypto.randomUUID(), selections: chosen };
      setPending(request);
      persist(`${storageKey}:proposal`, JSON.stringify(request));
      await result(api.POST("/heinzel/propose-connected-read-set", { body: { ...request, session_id: sessionId } }));
      forget(`${storageKey}:proposal`);
      setPending(null);
      setEntries([]);
      setReviewId(null);
      setReviewReady(false);
      setChatId(null);
      forget(`${storageKey}:review`);
      forget(`${storageKey}:chat`);
      await refresh();
    });
  const selectSuggestion = () =>
    void run(async () => {
      const shown = suggestion.data;
      const fresh = await result(
        api.GET("/heinzel/latest-source-set-suggestion-view", { params: { query: { session_id: sessionId } } }),
      );
      if (
        !shown ||
        shown.session_id !== sessionId ||
        canonical(shown) !== canonical(fresh) ||
        !shown.entries.every((entry) =>
          options.data?.some((option) => option.capability === entry.capability && option.method === entry.method),
        )
      )
        throw new Error("Suggestion changed or unavailable");
      setEntries(shown.entries);
    });
  const prepare = () =>
    void run(async () => {
      if (uncertain) throw new Error("The previous decision requires inspection");
      if (!proposal.data) throw new Error("No proposal");
      const request = reviewId ?? crypto.randomUUID();
      setReviewId(request);
      setReviewReady(false);
      persist(`${storageKey}:review`, request);
      await result(
        api.POST("/heinzel/prepare-connected-read-set-review", {
          body: {
            session_id: sessionId,
            request_id: request,
            proposal_request: proposal.data.request_id,
            proposal_revision: proposal.data.revision,
          },
        }),
      );
      setReviewReady(true);
      await client.invalidateQueries({ queryKey: [...key, "review", request] });
    });
  const stage = () =>
    void run(async () => {
      if (uncertain) throw new Error("The previous decision requires inspection");
      if (!review.data) throw new Error("No review");
      const value = await result(
        api.POST("/system/runner/chat/propose-connected-read-set-review", {
          params: { query: { durable_session: sessionId, request_id: review.data.request_id } },
        }),
      );
      if (value.personal_session_id !== sessionId || value.personal_owner_id !== owner)
        throw new Error("Unexpected conversation");
      client.setQueryData([...key, "chat", value.id], value);
      persist(`${storageKey}:chat`, value.id);
      setChatId(value.id);
    });
  const decide = (confirm: boolean) =>
    void run(async () => {
      if (!chat.data || !action || !review.data) throw new Error("Review unavailable");
      if (confirm) {
        const fresh = await result(
          api.GET("/heinzel/connected-read-set-review", {
            params: { query: { session_id: sessionId, request_id: review.data.request_id } },
          }),
        );
        if (canonical(fresh) !== canonical(review.data)) throw new Error("Review changed");
      }
      const params = { query: { session: chat.data.id, action: action.id } };
      persist(`${storageKey}:decision`, "uncertain");
      setUncertain(true);
      const value: SchemaChatSession = await result(
        confirm
          ? api.POST("/system/runner/chat/confirm", { params })
          : api.POST("/system/runner/chat/decline", { params }),
      );
      if (value.id !== chat.data.id || value.personal_session_id !== sessionId || value.personal_owner_id !== owner)
        throw new Error("Unexpected conversation");
      const recorded = value.actions?.find(
        (item) =>
          item.id === action.id &&
          item.module === action.module &&
          item.operation === action.operation &&
          canonical(item.arguments) === canonical(action.arguments),
      );
      if (!recorded || recorded.status !== (confirm ? "done" : "declined"))
        throw new Error("Decision outcome is not terminal");
      client.setQueryData([...key, "chat", value.id], value);
      forget(`${storageKey}:decision`);
      setUncertain(false);
      await refresh();
    });
  const inspect = () =>
    void run(async () => {
      if (!chat.data || !action) throw new Error("Review unavailable");
      let value = await result(api.GET("/system/runner/chat/show", { params: { query: { session: chat.data.id } } }));
      if (value.id !== chat.data.id || value.personal_session_id !== sessionId || value.personal_owner_id !== owner)
        throw new Error("Unexpected conversation");
      const exact = (item: SchemaChatAction) =>
        item.id === action.id &&
        item.module === action.module &&
        item.operation === action.operation &&
        canonical(item.arguments) === canonical(action.arguments);
      let found = value.actions?.find(exact);
      if (!found) throw new Error("Unexpected action");
      if (found.status === "running") {
        value = await result(
          api.POST("/system/runner/chat/recover", { params: { query: { session: chat.data.id, action: action.id } } }),
        );
        if (value.id !== chat.data.id || value.personal_session_id !== sessionId || value.personal_owner_id !== owner)
          throw new Error("Unexpected conversation");
        found = value.actions?.find(exact);
        if (!found) throw new Error("Unexpected action");
      }
      client.setQueryData([...key, "chat", value.id], value);
      if (
        found.status === "done" ||
        found.status === "declined" ||
        found.status === "uncertain" ||
        found.status === "failed"
      ) {
        forget(`${storageKey}:decision`);
        setUncertain(false);
      }
      await refresh();
    });
  const newReview = () =>
    void run(async () => {
      if ((action?.status !== "uncertain" && action?.status !== "failed") || uncertain)
        throw new Error("Decision outcome needs inspection");
      forget(`${storageKey}:review`);
      forget(`${storageKey}:chat`);
      setReviewId(null);
      setReviewReady(false);
      setChatId(null);
    });
  const revoke = () =>
    void run(async () => {
      if (!approval.data) throw new Error("No verified approval");
      await result(
        api.POST("/heinzel/revoke-connected-read-set", {
          body: { session_id: sessionId, request_id: approval.data.request_id },
        }),
      );
      setReviewId(null);
      setReviewReady(false);
      setChatId(null);
      forget(`${storageKey}:review`);
      forget(`${storageKey}:chat`);
      await refresh();
    });
  return (
    <section aria-label="Sources for this chat" className="space-y-2 rounded border border-border p-3 text-sm">
      <h3 className="font-medium">Sources for this chat</h3>
      {!recovery.available && (
        <p role="alert">
          Review state could not be loaded. Restore browser storage and reopen this conversation before making changes.
        </p>
      )}
      <p>
        Choose up to four sources to review together. Suggestions do not grant access. Your approval applies to these
        sources in direct chat; it does not enable scheduled access.
      </p>
      {!suggestion.data && !suggestion.isError && (
        <div>
          <p>
            Tell your Heinzel in the chat which information you want to use, and ask it to suggest sources for review.
          </p>
          {onAsk && (
            <Button type="button" size="sm" variant="outline" onClick={onAsk}>
              Ask about sources
            </Button>
          )}
        </div>
      )}
      {suggestion.data?.session_id === sessionId && (
        <div className="space-y-1">
          <p>Suggested sources: no provider was contacted and no access granted.</p>
          <p className="whitespace-pre-wrap break-words">
            {suggestion.data.entries
              .map((entry) => `${describe(entry)}\n${argumentsSummary(entry.fixed_arguments)}`)
              .join("\n\n")}
          </p>
          <Button type="button" size="sm" disabled={busy || !!pending} onClick={selectSuggestion}>
            Use suggested sources
          </Button>
        </div>
      )}
      <details className="space-y-2">
        <summary>Advanced: choose sources manually</summary>
        <label className="block">
          Read operation{" "}
          <select
            className="w-full rounded border p-1"
            value={selection}
            onChange={(event) => setSelection(event.target.value)}
            disabled={busy || !!pending}
          >
            <option value="">Choose an operation</option>
            {options.data?.map((option) => (
              <option key={`${option.capability}.${option.method}`} value={`${option.capability}.${option.method}`}>
                {option.capability}.{option.method} · {option.provider}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          Fixed arguments (JSON)
          <textarea
            className="w-full rounded border p-1"
            value={argumentsText}
            onChange={(event) => setArgumentsText(event.target.value)}
            disabled={busy || !!pending}
          />
        </label>
        <Button
          type="button"
          size="sm"
          disabled={busy || !!pending || !selection || entries.length >= 4}
          onClick={() => {
            try {
              const option = options.data?.find((item) => `${item.capability}.${item.method}` === selection);
              const args: unknown = JSON.parse(argumentsText);
              if (!option || !args || typeof args !== "object" || Array.isArray(args)) throw new Error();
              setEntries([
                ...entries,
                {
                  capability: option.capability,
                  method: option.method,
                  fixed_arguments: args as SchemaReadSetSelection["fixed_arguments"],
                },
              ]);
              setError(false);
            } catch {
              setError(true);
            }
          }}
        >
          Add source
        </Button>
      </details>
      {!!entries.length && (
        <>
          <p>Selected sources:</p>
          <p className="whitespace-pre-wrap break-words">
            {entries.map((entry) => `${describe(entry)}\n${argumentsSummary(entry.fixed_arguments)}`).join("\n\n")}
          </p>
          <details>
            <summary>Exact selected arguments</summary>
            <pre className="overflow-x-auto whitespace-pre-wrap break-all">{JSON.stringify(entries, null, 2)}</pre>
          </details>
          <Button type="button" size="sm" variant="outline" disabled={busy || !!pending} onClick={() => setEntries([])}>
            Clear selection
          </Button>
        </>
      )}
      <Button type="button" size="sm" disabled={busy || (!entries.length && !pending)} onClick={() => propose(entries)}>
        {pending ? "Retry the same proposal" : "Prepare set"}
      </Button>
      {pending && <p>Retry uses the same request and terms. Inspect the recorded proposal if the response was lost.</p>}
      {proposal.data && (
        <div>
          <p>Sources ready for review. These terms are not an approval.</p>
          {proposal.data.entries.map((entry) => (
            <div key={entry.handle} className="break-words">
              <p>
                {describe(entry.terms)} · Account:{" "}
                {entry.terms.review.account_display || entry.terms.review.account_identity}
              </p>
              <p>{argumentsSummary(entry.terms.review.fixed_arguments)}</p>
              <details>
                <summary>Exact source and access details</summary>
                <pre className="overflow-x-auto whitespace-pre-wrap break-all">
                  {JSON.stringify(entry.terms, null, 2)}
                </pre>
              </details>
            </div>
          ))}
          <Button type="button" size="sm" disabled={busy} onClick={prepare}>
            {reviewId ? "Retry review" : "Check current sources"}
          </Button>
        </div>
      )}
      {review.data && !chatId && (
        <Button type="button" size="sm" disabled={busy} onClick={stage}>
          Review set access
        </Button>
      )}
      {action?.status === "pending" && !uncertain && (
        <ol>
          <ActionCard
            action={{ ...action, arguments: {}, command: "Approve these sources for this chat" }}
            busy={busy}
            proposalTitle="Review these sources"
            onDecide={decide}
          />
        </ol>
      )}
      {action?.status === "done" && (
        <p role="status">Confirmation recorded. Sources and account are checked again before each read.</p>
      )}
      {action?.status === "declined" && <p>Approval was declined.</p>}
      {action?.status === "failed" && (
        <p role="alert">The approval action failed. Inspect the current approval before starting another review.</p>
      )}
      {approval.data && (
        <p role="status">The whole set has a recorded approval. Each read rechecks its source and account.</p>
      )}
      {proposal.data && approval.isError && <p>No current set approval could be verified.</p>}
      {(action?.status === "running" || action?.status === "uncertain") && !uncertain && (
        <p role="alert">The outcome needs inspection; confirmation will not be repeated automatically.</p>
      )}
      {action?.status === "running" && !uncertain && (
        <Button type="button" size="sm" disabled={busy} onClick={inspect}>
          Check decision outcome
        </Button>
      )}
      {(action?.status === "uncertain" || action?.status === "failed") && !uncertain && (
        <Button type="button" size="sm" disabled={busy} onClick={newReview}>
          Start a new review
        </Button>
      )}
      {uncertain && (
        <div role="alert">
          <p>The decision outcome needs inspection; it will not be repeated automatically.</p>
          <Button type="button" size="sm" disabled={busy || !action} onClick={inspect}>
            Check decision outcome
          </Button>
        </div>
      )}
      {approval.data && (
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={revoke}>
          Revoke this set
        </Button>
      )}
      {(error || proposal.isError || suggestion.isError || options.isError || review.isError || chat.isError) && (
        <p role="alert">
          The action or current terms could not be verified. Inspect the saved outcome before deciding again.
        </p>
      )}
    </section>
  );
}
