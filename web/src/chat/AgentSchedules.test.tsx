import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { AgentSchedules } from "./AgentSchedules";

it("requires an explicit choice before including a connected read in a new schedule", async () => {
  const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
  const contract = {
    session_id: session,
    request_id: "contract",
    preview_sha256: "digest",
    timeout_seconds: 60,
    max_usd: null,
  };
  const grant = { request_id: "grant", capability: "sample", method: "read", review: { session } };
  let submitted: Record<string, unknown> | null = null;
  mockApi({
    "/api/heinzel/latest-scheduled-agent-proposal": () => contract,
    "/api/heinzel/agent-contract-review": () => ({ proposal: contract, accepted: true }),
    "/api/heinzel/connected-read-state": () => ({ session_id: session, status: "approved", proposal: grant }),
    "/api/heinzel/agent-schedules": () => ({ session_id: session, items: [], next_cursor: null }),
    "/api/heinzel/propose-agent-schedule": async (req: Request) => {
      submitted = await req.clone().json();
      return { ...submitted, connected_grant: grant };
    },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <AgentSchedules sessionId={session} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await user.click(screen.getByText("Recurring agent turns"));
  const choice = await screen.findByRole("checkbox", { name: /Allow the separately approved sample.read/ });
  expect(choice).not.toBeChecked();
  await user.click(choice);
  await user.click(await screen.findByRole("button", { name: "Propose recurring turns" }));
  expect(submitted).toMatchObject({ session_id: session, connected_request_id: "grant" });
});

it("uses an explicit interval suggestion only after a human chooses it, while keeping cron inert", async () => {
  const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
  const contract = {
    session_id: session,
    request_id: "contract",
    preview_sha256: "digest",
    timeout_seconds: 60,
    max_usd: null,
  };
  let trigger: Record<string, unknown> = { kind: "interval", cadence_seconds: 420 };
  let submitted: Record<string, unknown> | null = null;
  mockApi({
    "/api/heinzel/latest-scheduled-agent-proposal": () => contract,
    "/api/heinzel/agent-contract-review": () => ({ proposal: contract, accepted: true }),
    "/api/heinzel/connected-read-state": () => ({ session_id: session, status: "none", proposal: null }),
    "/api/heinzel/agent-schedules": () => ({ session_id: session, items: [], next_cursor: null }),
    "/api/heinzel/latest-schedule-suggestion-view": () => ({
      session_id: session,
      request_id: "suggestion",
      revision: 1,
      status: "suggested",
      trigger,
      suggested_reads: [],
    }),
    "/api/heinzel/propose-agent-schedule": async (req: Request) => {
      submitted = await req.clone().json();
      return { ...submitted, connected_grant: null };
    },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <AgentSchedules sessionId={session} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await user.click(screen.getByText("Recurring agent turns"));
  const interval = await screen.findByText(/Suggested interval: every 7 minutes/);
  expect(interval).toBeInTheDocument();
  expect(screen.getByLabelText("Every (minutes)")).toHaveValue(5);
  await user.click(screen.getByRole("button", { name: "Use interval in preview" }));
  expect(screen.getByLabelText("Every (minutes)")).toHaveValue(7);
  expect(submitted).toBeNull();
  await user.click(screen.getByRole("button", { name: "Propose recurring turns" }));
  expect(submitted).toMatchObject({ cadence_seconds: 420 });
  trigger = { cron: "*/7 * * * *", timezone: "UTC" };
  await user.click(screen.getByRole("button", { name: "Refresh suggestion" }));
  expect(await screen.findByText(/Calendar schedules are not supported here/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Use interval in preview" })).not.toBeInTheDocument();
});
