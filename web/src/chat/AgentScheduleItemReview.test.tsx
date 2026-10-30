import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { AgentScheduleReview } from "./AgentScheduleReview";

it("describes the exact observed-item policy and provider identity fields in schedule review", async () => {
  const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
  const proposal = {
    session_id: session,
    request_id: "schedule",
    cadence_seconds: 300,
    max_requests: 2,
    timeout_seconds: 30,
    notification_policy: {
      max_per_24h: 2,
      topic_cooldown_seconds: 60,
      item_evidence: { deduplicate_by: "revision" },
    },
    connected_grant: {
      capability: "calendar",
      method: "events",
      review: {
        account_identity: "account",
        item_identity: { collection: ["events"], identity_field: "id", revision_field: "version" },
      },
    },
  };
  mockApi({
    "/api/heinzel/agent-schedule": () => ({ binding: proposal, status: "proposed" }),
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <AgentScheduleReview sessionId={session} proposal={proposal as never} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/Only an item observed in the current turn/)).toHaveTextContent("item revision");
  expect(screen.getByText(/Provider-reported collection events/)).toHaveTextContent("revision field version");
  expect(screen.queryByText(/same model-chosen topic/)).not.toBeInTheDocument();
});
