import { useQuery } from "@tanstack/react-query";
import { api, problem } from "@/api/client";
import { AgentContractAcceptance } from "@/chat/AgentContractAcceptance";

/** The stored proposal is a draft, never a claim that the agent or its tools are enabled. */
export function AgentProposalSummary({ sessionId }: { sessionId: string }) {
  const query = useQuery({
    queryKey: ["heinzel", "agent-proposal", sessionId],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/heinzel/latest-scheduled-agent-proposal", {
        params: { query: { session_id: sessionId } },
      });
      if (!response.ok || data === undefined) throw problem(response.status, error);
      if (data && (data.session_id !== sessionId || data.status !== "proposed")) throw new Error("Invalid proposal");
      return data;
    },
    retry: false,
  });
  if (!query.data || query.data.session_id !== sessionId) return null;
  return (
    <section aria-label="Proposed agent terms" className="space-y-1 rounded border border-border p-3 text-sm">
      <h3 className="font-medium">Proposed agent terms</h3>
      <p>Revision {query.data.revision} · proposed terms</p>
      <details>
        <summary className="cursor-pointer">Review proposed terms</summary>
        <p className="whitespace-pre-wrap break-words">{query.data.contract}</p>
        <p>
          {query.data.max_usd === null
            ? "Subscription plan · cost unknown"
            : `Proposed API limit: $${query.data.max_usd} per turn`}
          {` · ${query.data.timeout_seconds} seconds. These terms are not enforced yet.`}
        </p>
        <p>This proposal does not activate the agent or grant tool permissions.</p>
      </details>
      <AgentContractAcceptance sessionId={sessionId} proposal={query.data} />
    </section>
  );
}
