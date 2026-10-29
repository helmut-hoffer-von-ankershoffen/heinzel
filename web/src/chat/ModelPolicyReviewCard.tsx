import type { SchemaChatAction } from "@/api/client";

/** An inert preview until the selected access and credential binding can be verified. */
export function ModelPolicyReviewCard({ action, sessionId }: { action: SchemaChatAction; sessionId: string }) {
  const args = action.arguments;
  const review = args?.review;
  if (
    action.module !== "heinzel" ||
    action.operation !== "approve-model-grant" ||
    action.status !== "pending" ||
    args?.session_id !== sessionId ||
    typeof args.request_id !== "string" ||
    Object.hasOwn(args, "approval_proof") ||
    !review ||
    typeof review !== "object" ||
    Array.isArray(review) ||
    !("request_id" in review) ||
    review.request_id !== args.request_id
  )
    return null;

  return (
    <section aria-label="Model policy review" className="space-y-2 rounded border border-border p-3 text-sm">
      <h3 className="font-medium">Model policy awaiting review</h3>
      <p>Review the proposed model, limits and tool permissions for this agent.</p>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-background p-2 text-xs">
        {JSON.stringify(review, null, 2)}
      </pre>
      <p>Approval is unavailable here until the selected access and credential binding are verified.</p>
    </section>
  );
}
