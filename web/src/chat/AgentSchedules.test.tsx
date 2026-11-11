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
  await user.click(screen.getByText("Recurring Heinzel turns"));
  const choice = await screen.findByRole("checkbox", { name: /Allow the separately approved sample.read/ });
  expect(choice).not.toBeChecked();
  await user.click(choice);
  await user.click(await screen.findByRole("button", { name: "Propose recurring turns" }));
  expect(submitted).toMatchObject({ session_id: session, connected_request_id: "grant" });
});

it.each([false, true])("proposes the chosen interval with quiet hours enabled: %s", async (quiet) => {
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
  let rejectQuiet = quiet;
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
      if (rejectQuiet) {
        rejectQuiet = false;
        return Response.json(
          { detail: [{ loc: ["body", "quiet_hours"], msg: "Time zone unavailable" }] },
          { status: 422 },
        );
      }
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
  await user.click(screen.getByText("Recurring Heinzel turns"));
  const interval = await screen.findByText(/Suggested interval: every 7 minutes/);
  expect(interval).toBeInTheDocument();
  expect(screen.getByLabelText("Every (minutes)")).toHaveValue(5);
  await user.click(screen.getByRole("button", { name: "Use interval in preview" }));
  expect(screen.getByLabelText("Every (minutes)")).toHaveValue(7);
  expect(submitted).toBeNull();
  if (quiet) {
    await user.click(screen.getByRole("checkbox", { name: "Skip scheduled turns during quiet hours" }));
    const zone = screen.getByLabelText("Time zone");
    await user.clear(zone);
    await user.type(zone, "Europe/Berln");
    expect(screen.getByRole("button", { name: "Propose recurring turns" })).toBeDisabled();
    expect(submitted).toBeNull();
    await user.clear(zone);
    await user.type(zone, "Europe/Berlin");
  }
  await user.click(screen.getByRole("button", { name: "Propose recurring turns" }));
  if (quiet) {
    expect(await screen.findByText("Enter a valid time zone such as Europe/Berlin.")).toHaveAttribute("role", "alert");
    expect(screen.getByLabelText("Time zone")).toBeEnabled();
    expect(sessionStorage.getItem(`hz.agent-schedule.proposal.${session}`)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Propose recurring turns" }));
  }
  expect(submitted).toMatchObject({ cadence_seconds: 420 });
  if (quiet) {
    expect(submitted).toMatchObject({ quiet_hours: { start: "22:00", end: "07:00", timezone: "Europe/Berlin" } });
  } else {
    expect(submitted).not.toHaveProperty("quiet_hours");
  }
  trigger = { cron: "*/7 * * * *", timezone: "UTC" };
  await user.click(screen.getByRole("button", { name: "Refresh suggestion" }));
  expect(await screen.findByText(/Calendar schedules are not supported here/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Use interval in preview" })).not.toBeInTheDocument();
});
