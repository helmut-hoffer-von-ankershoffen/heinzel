import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import { AgentSchedules } from "@/chat/AgentSchedules";
import { ScheduledNotificationAttempts } from "@/chat/ScheduledNotificationAttempts";
import { mockApi } from "@/test/render";

const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const other = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
const proposal = {
  session_id: session,
  request_id: "contract",
  preview_sha256: "digest",
  timeout_seconds: 60,
  max_usd: null,
};
const draft = {
  session_id: session,
  request_id: "notice-1",
  topic: "example",
  title: "Title",
  body: "Private",
  status: "proposed",
};

function show(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

it("requires a separate opt-in and submits exact bounded terms for review", async () => {
  let body: Record<string, unknown> | undefined;
  mockApi({
    "/api/heinzel/latest-scheduled-agent-proposal": () => proposal,
    "/api/heinzel/agent-contract-review": () => ({ proposal, accepted: true }),
    "/api/heinzel/connected-read-state": () => ({ session_id: session, status: "none", proposal: null }),
    "/api/heinzel/agent-schedules": () => ({ session_id: session, items: [], next_cursor: null }),
    "/api/heinzel/scheduled-notification-attempts": () => ({ session_id: session, items: [], next_cursor: null }),
    "/api/heinzel/propose-agent-schedule": async (request: Request) => {
      body = await request.clone().json();
      return { ...body, connected_grant: null };
    },
  });
  const user = userEvent.setup();
  show(<AgentSchedules sessionId={session} />);
  await user.click(screen.getByText("Recurring agent turns"));
  const enabled = screen.getByRole("checkbox", { name: /Allow this agent to queue local notifications/ });
  expect(enabled).not.toBeChecked();
  await user.click(enabled);
  await user.clear(screen.getByLabelText("Maximum attempts per rolling 24 hours"));
  await user.type(screen.getByLabelText("Maximum attempts per rolling 24 hours"), "4");
  await user.click(await screen.findByRole("button", { name: "Propose recurring turns" }));
  expect(body).toMatchObject({
    session_id: session,
    notification_policy: { max_per_24h: 4, topic_cooldown_seconds: 3600, per_turn: 1, delivery: "local-notification" },
  });
  expect(screen.getByText(/Topics are model-chosen, not verified source identities/)).toBeInTheDocument();
});

it("requires acknowledgment for an unresolved attempt and never resends", async () => {
  let resolved = false;
  const { calls } = mockApi({
    "/api/heinzel/scheduled-notification-attempts": () => ({
      session_id: session,
      items: [{ proposal: draft, seq: 1, status: resolved ? "abandoned" : "unresolved" }],
      next_cursor: null,
    }),
    "/api/heinzel/resolve-scheduled-notification": async (request: Request) => {
      expect(await request.clone().json()).toEqual({
        session_id: session,
        request_id: "notice-1",
        acknowledge_uncertain: true,
      });
      resolved = true;
      return { proposal: draft, seq: 1, status: "abandoned" };
    },
  });
  const user = userEvent.setup();
  show(<ScheduledNotificationAttempts sessionId={session} />);
  const panel = await screen.findByRole("region", { name: "Scheduled notification activity" });
  const resolve = await within(panel).findByRole("button", { name: "Inspect or set aside outcome" });
  expect(resolve).toBeDisabled();
  await user.click(
    within(panel).getByRole("checkbox", { name: /I understand this notice may already have been queued/ }),
  );
  await user.click(resolve);
  expect(await within(panel).findByText(/Topic: example · abandoned/)).toBeInTheDocument();
  expect(calls.filter((call) => new URL(call.url).pathname.endsWith("resolve-scheduled-notification"))).toHaveLength(1);
  expect(calls.some((call) => /\/(send|confirm)$/.test(new URL(call.url).pathname))).toBe(false);
});

it("does not render or resolve another owner's attempt", async () => {
  const { calls } = mockApi({
    "/api/heinzel/scheduled-notification-attempts": () => ({
      session_id: session,
      items: [{ proposal: { ...draft, session_id: other }, seq: 1, status: "unresolved" }],
      next_cursor: null,
    }),
  });
  show(<ScheduledNotificationAttempts sessionId={session} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be verified");
  expect(screen.queryByText("Private")).not.toBeInTheDocument();
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("resolve-scheduled-notification"))).toBe(false);
});

it("recovers a lost acknowledgment by reading history without repeating the decision", async () => {
  let recorded = false;
  const { calls } = mockApi({
    "/api/heinzel/scheduled-notification-attempts": () => ({
      session_id: session,
      items: [{ proposal: draft, seq: 1, status: recorded ? "recorded" : "unresolved" }],
      next_cursor: null,
    }),
    "/api/heinzel/resolve-scheduled-notification": () => {
      recorded = true;
      return { status: 503, body: { error: "response lost" } };
    },
  });
  const user = userEvent.setup();
  const mounted = show(<ScheduledNotificationAttempts sessionId={session} />);
  const panel = await screen.findByRole("region", { name: "Scheduled notification activity" });
  await user.click(
    await within(panel).findByRole("checkbox", { name: /I understand this notice may already have been queued/ }),
  );
  await user.click(within(panel).getByRole("button", { name: "Inspect or set aside outcome" }));
  expect(await within(panel).findByText(/queued; not confirmed shown/)).toBeInTheDocument();
  mounted.unmount();
  show(<ScheduledNotificationAttempts sessionId={session} />);
  expect(await screen.findByText(/Topic: example · queued; not confirmed shown/)).toBeInTheDocument();
  expect(calls.filter((call) => new URL(call.url).pathname.endsWith("resolve-scheduled-notification"))).toHaveLength(1);
});
