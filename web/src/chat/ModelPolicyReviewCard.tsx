import type { SchemaChatAction } from "@/api/client";
import { Button } from "@/components/ui/button";

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Restrict confirmation to the binding and tool-free policy this host currently enforces. */
export function supportedPolicy(review: unknown): boolean {
  if (!object(review) || !object(review.binding) || !object(review.allowed_tools)) return false;
  const binding = review.binding;
  return (
    review.access === "pydantic-ai" &&
    review.model === "openai:gpt-4.1-mini-2025-04-14" &&
    binding.access === review.access &&
    binding.model === review.model &&
    binding.endpoint === "https://api.openai.com/v1" &&
    typeof binding.owner === "string" &&
    /^u-[0-9a-f]{32}$/.test(binding.owner) &&
    typeof binding.credential_fingerprint === "string" &&
    /^[0-9a-f]{64}$/.test(binding.credential_fingerprint) &&
    Object.keys(review.allowed_tools).length === 0 &&
    Array.isArray(review.allowed_skill_tools) &&
    review.allowed_skill_tools.length === 0
  );
}

/** Show the exact requested policy; server-side confirmation rechecks the selected access and binding. */
export function ModelPolicyReviewCard({
  action,
  sessionId,
  busy,
  onConfirm,
}: {
  action: SchemaChatAction;
  sessionId: string;
  busy: boolean;
  onConfirm: () => void;
}) {
  const args = action.arguments;
  const review = args?.review;
  if (
    action.module !== "heinzel" ||
    action.operation !== "approve-model-grant" ||
    action.status !== "pending" ||
    args?.session_id !== sessionId ||
    typeof args.request_id !== "string" ||
    Object.hasOwn(args, "approval_proof") ||
    !object(review) ||
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
      <p>
        The credential fingerprint identifies the reviewed configuration, not a provider account. Changes apply to
        subsequent requests; a request already admitted may finish.
      </p>
      {supportedPolicy(review) ? (
        <Button type="button" size="sm" disabled={busy} onClick={onConfirm}>
          Confirm model policy
        </Button>
      ) : (
        <p>Confirmation is unavailable for this model policy.</p>
      )}
    </section>
  );
}
