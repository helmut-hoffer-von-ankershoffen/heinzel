import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, authorityMiddleware, type SchemaChatAction, type SchemaChatSession, unwrap } from "@/api/client";
import { authorityEpoch } from "@/api/token";
import { Button } from "@/components/ui/button";

const activityLabels: Record<string, string> = {
  "draft.release-observed": "Release observed",
  "draft.append-attempted": "Draft append attempted",
  "draft.append-acknowledged": "Document draft recorded",
  "draft.append-uncertain": "Draft append outcome unknown",
  "draft.review-notice-attempted": "Review notice attempted",
  "draft.review-notice-acknowledged": "Review notice acknowledged",
  "draft.review-notice-uncertain": "Review notice outcome unknown",
  "draft.share-attempted": "Link delivery attempted",
  "draft.share-acknowledged": "Link delivery acknowledged",
  "draft.share-uncertain": "Link delivery outcome unknown",
  "draft.confirmation-notice-attempted": "Confirmation notice attempted",
  "draft.confirmation-notice-acknowledged": "Confirmation notice acknowledged",
  "draft.confirmation-notice-uncertain": "Confirmation notice outcome unknown",
};

const digest = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const nonempty = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const object = (value: unknown): Record<string, unknown> | undefined =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;

/** Validate the owner-scoped server action before offering any decision, including on reload. */
export function releaseDraftReview(action: SchemaChatAction, sessionId: string) {
  const args = action.arguments;
  const review = object(args?.review);
  const snapshot = object(review?.snapshot);
  const document = object(snapshot?.target);
  const channel = object(review?.channel_target);
  if (
    action.module !== "heinzel" ||
    action.operation !== "share-draft-link" ||
    args?.session_id !== sessionId ||
    !digest(args.request_id) ||
    review?.session_id !== sessionId ||
    !digest(review.draft_key) ||
    snapshot?.draft_key !== review.draft_key ||
    !nonempty(snapshot.text) ||
    !nonempty(document?.reference) ||
    !nonempty(document.revision) ||
    !nonempty(document.section) ||
    !nonempty(review.channel) ||
    !nonempty(channel?.account) ||
    !nonempty(channel.destination) ||
    !nonempty(channel.scope) ||
    review.link_text !== document.reference
  ) {
    return undefined;
  }
  return {
    draftKey: review.draft_key,
    requestId: args.request_id,
    text: snapshot.text,
    document: document.reference,
    revision: document.revision,
    section: document.section,
    channel: review.channel,
    account: channel.account,
    destination: channel.destination,
    scope: channel.scope,
    link: review.link_text,
  };
}

export function ReleaseDraftReviewDetails({ review }: { review: NonNullable<ReturnType<typeof releaseDraftReview>> }) {
  return (
    <div className="mt-2 space-y-2 break-words">
      <p>Confirm to send the document link. The document can change after this review.</p>
      <dl className="space-y-1">
        {[
          ["Document", review.document],
          ["Section", review.section],
          ["Reviewed revision", review.revision],
          ["Channel", review.channel],
          ["Posting account", review.account],
          ["Destination", review.destination],
          ["Posting scope", review.scope],
          ["Link to send", review.link],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="font-medium">{label}</dt>
            <dd className="break-all">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="font-medium">Edited draft at this revision</p>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-background p-2">
        {review.text}
      </pre>
    </div>
  );
}

/** Read private journal activity and stage a review. Only the conversation can decide the resulting action. */
export function ReleaseDraftActivity({
  authority,
  sessionId,
  conversationBusy,
  onReview,
}: {
  authority: number;
  sessionId: string;
  conversationBusy: boolean;
  onReview: (chat: SchemaChatSession) => void;
}) {
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string>();
  const mutation = useRef<AbortController | null>(null);
  useEffect(() => () => mutation.current?.abort(), []);
  const query = useQuery({
    queryKey: ["heinzel", "release-draft-activity", authority, sessionId],
    enabled: open,
    gcTime: 0,
    retry: false,
    queryFn: async ({ signal }) => {
      const items = await unwrap(
        api.GET("/heinzel/release-draft-activity", {
          params: { query: { session_id: sessionId } },
          middleware: authorityMiddleware(authority),
          signal,
        }),
      );
      if (authorityEpoch() !== authority) throw new Error("Release draft activity scope changed");
      return { items, isCurrent: () => authorityEpoch() === authority };
    },
  });

  async function stage(draftKey: string) {
    if (conversationBusy || mutation.current || authorityEpoch() !== authority) return;
    const controller = new AbortController();
    mutation.current = controller;
    const current = () => !controller.signal.aborted && authorityEpoch() === authority;
    setWorking(true);
    setMessage(undefined);
    try {
      const requestId = await unwrap(
        api.POST("/heinzel/prepare-release-draft-share", {
          body: { session_id: sessionId, draft_key: draftKey },
          middleware: authorityMiddleware(authority),
          signal: controller.signal,
        }),
      );
      if (!current()) return;
      if (!digest(requestId)) throw new Error("The prepared draft identity was unavailable.");
      const chat = await unwrap(
        api.POST("/system/runner/chat/propose-release-draft-share", {
          body: { durable_session: sessionId, request_id: requestId },
          middleware: authorityMiddleware(authority),
          signal: controller.signal,
        }),
      );
      if (!current()) return;
      const actions = (chat.actions ?? []).filter(
        (action) =>
          action.module === "heinzel" &&
          action.operation === "share-draft-link" &&
          action.arguments?.session_id === sessionId &&
          action.arguments?.request_id === requestId,
      );
      const action = actions[0];
      const review = action && releaseDraftReview(action, sessionId);
      if (
        chat.personal_session_id !== sessionId ||
        actions.length !== 1 ||
        action?.status !== "pending" ||
        review?.draftKey !== draftKey
      ) {
        setMessage("A new review is unavailable. Check the recorded draft activity for its outcome.");
        return;
      }
      onReview(chat);
    } catch {
      if (current()) setMessage("Couldn’t open the release draft review.");
    } finally {
      if (mutation.current === controller) mutation.current = null;
      if (current()) setWorking(false);
    }
  }

  const items = !query.isError && query.data?.isCurrent() ? query.data.items : undefined;
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Release draft activity</summary>
      {open && (
        <div className="space-y-2 text-sm">
          {message && <p role="alert">{message}</p>}
          {query.isFetching && <p role="status">Checking release draft activity…</p>}
          {query.isError && <p role="alert">Couldn’t check release draft activity.</p>}
          {items?.length === 0 && <p>No recorded release drafts.</p>}
          {items && (
            <ul className="space-y-2">
              {[...items].reverse().map(({ kind, draft_key: key, at }) => {
                if (!nonempty(kind) || !Object.hasOwn(activityLabels, kind) || !digest(key) || !nonempty(at))
                  return null;
                return (
                  <li key={`${kind}:${key}:${at}`} className="space-y-1 break-words">
                    <strong>{activityLabels[kind]}</strong>
                    <time className="block text-xs text-muted-foreground" dateTime={at}>
                      {new Date(at).toLocaleString()}
                    </time>
                    {kind === "draft.append-acknowledged" && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={working || conversationBusy}
                        onClick={() => void stage(key)}
                      >
                        Review draft share
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <Button size="sm" variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>
            Refresh
          </Button>
        </div>
      )}
    </details>
  );
}
