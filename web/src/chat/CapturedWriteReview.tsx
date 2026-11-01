import { useRef, useState } from "react";
import type { SchemaChatAction } from "@/api/client";
import { Button } from "@/components/ui/button";

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** An explicit human decision, with a persistent fence before any confirmation request. */
export function CapturedWriteReview({
  action,
  sessionId,
  chatId,
  onConfirm,
}: {
  action: SchemaChatAction;
  sessionId: string;
  chatId: string;
  onConfirm: () => Promise<void>;
}) {
  const args = action.arguments;
  const review = args?.review;
  const valid =
    action.module === "heinzel" &&
    action.operation === "execute-captured-write" &&
    args?.session_id === sessionId &&
    typeof args.request_id === "string" &&
    object(review) &&
    review.session === sessionId &&
    review.request_id === args.request_id &&
    Object.keys(args)
      .sort((left, right) => left.localeCompare(right))
      .join(",") === "request_id,review,session_id" &&
    ["account", "source", "reference", "operation", "section", "base_revision", "write_scope", "content"].every(
      (key) => typeof review[key] === "string" && review[key] !== "",
    );
  const fence = `captured-write:${sessionId}:${chatId}:${action.id}`;
  const [attempted, setAttempted] = useState(() => {
    try {
      return sessionStorage.getItem(fence) !== null;
    } catch {
      return true;
    }
  });
  const [busy, setBusy] = useState(false);
  const entered = useRef(false);
  if (!valid) return null;
  const confirm = async () => {
    if (entered.current || busy || attempted || action.status !== "pending") return;
    entered.current = true;
    try {
      sessionStorage.setItem(fence, "attempted");
      if (sessionStorage.getItem(fence) !== "attempted") throw new Error("storage unavailable");
    } catch {
      setAttempted(true);
      return;
    }
    setAttempted(true);
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-label="Captured write review" className="space-y-2 rounded border border-warn p-3 text-sm">
      <h3 className="font-medium">Review this proposed replacement</h3>
      <p>Current section text is not shown. Review the full replacement and base revision before confirming.</p>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-background p-2 text-xs">
        {JSON.stringify(review, null, 2)}
      </pre>
      {action.status === "pending" && !attempted ? (
        <Button type="button" size="sm" disabled={busy} onClick={() => void confirm()}>
          Confirm replacement
        </Button>
      ) : action.status === "done" ? (
        <p role="status">The confirmed attempt was acknowledged. Inspect its recorded receipt for details.</p>
      ) : (
        <p role="status">Confirmation cannot be repeated here. Inspect the recorded outcome before a new review.</p>
      )}
    </section>
  );
}
