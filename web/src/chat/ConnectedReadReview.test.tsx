import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { ConnectedReadReview } from "./ConnectedReadReview";

const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const proposal = {
  request_id: "r1",
  revision: 1,
  capability: "example",
  method: "read",
  binding_sha256: "binding",
  review: {
    owner: "owner",
    session,
    operation: "capability-method:example.read",
    schema_sha256: "schema",
    source_revision: "source",
    account_identity: "account",
    provider_scopes: ["read"],
    candidate_read_disclosure: "Candidate metadata is filtered locally.",
    item_identity: { collection: ["events"], identity_field: "id", revision_field: "version" },
    fixed_arguments: { period: "today" },
  },
};
function show() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <ConnectedReadReview sessionId={session} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("discovers a recorded owner proposal and requires an exact action review", async () => {
  const action = {
    id: "a1",
    module: "heinzel",
    operation: "approve-connected-read",
    status: "pending",
    command: "approve",
    arguments: { session_id: session, request_id: "r1", review: proposal },
  };
  const chat = { id: "chat", personal_session_id: session, actions: [action], messages: [] };
  const { calls } = mockApi({
    "/api/heinzel/connected-read-options": () => [],
    "/api/heinzel/connected-read-state": () => ({
      session_id: session,
      status: "proposed",
      proposal,
      descriptor: null,
    }),
    "/api/heinzel/connected-read-review": () => proposal,
    "/api/system/runner/chat/propose-connected-read-review": () => chat,
    "/api/system/runner/chat/show": () => chat,
    "/api/system/runner/chat/confirm": () => ({ ...chat, actions: [{ ...action, status: "done" }] }),
  });
  const user = userEvent.setup();
  show();
  expect(await screen.findByText(/Account: account/)).toBeInTheDocument();
  expect(
    screen.getByText(/Provider-reported candidate read: Candidate metadata is filtered locally/),
  ).toBeInTheDocument();
  expect(screen.getByText(/Provider-reported item identity: collection events/)).toHaveTextContent(
    "revision field version",
  );
  await user.click(await screen.findByRole("button", { name: "Review access" }));
  expect(await screen.findByText("Review connected read")).toBeInTheDocument();
  expect(calls.some((r) => new URL(r.url).pathname.endsWith("/confirm"))).toBe(false);
  await user.click(screen.getByRole("button", { name: /confirm/i }));
  expect(await screen.findByText(/This review was confirmed/)).toBeInTheDocument();
});

it("does not offer review when provider and proposal are absent", async () => {
  mockApi({
    "/api/heinzel/connected-read-options": () => [],
    "/api/heinzel/connected-read-state": () => ({
      session_id: session,
      status: "none",
      proposal: null,
      descriptor: null,
    }),
  });
  show();
  expect(await screen.findByText(/No selected provider/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review access" })).not.toBeInTheDocument();
});

it("retains an uncertain proposal and retries its exact request after reload", async () => {
  sessionStorage.clear();
  let writes = 0;
  const bodies: unknown[] = [];
  mockApi({
    "/api/heinzel/connected-read-options": () => [
      { capability: "example", method: "read", provider: "fixture", source: "fixture", input_schema: {} },
    ],
    "/api/heinzel/connected-read-state": () => ({
      session_id: session,
      status: "none",
      proposal: null,
      descriptor: null,
    }),
    "/api/heinzel/propose-connected-read": async (request: Request) => {
      bodies.push(await request.clone().json());
      writes += 1;
      return writes === 1 ? { status: 503, body: { error: "unknown" } } : proposal;
    },
  });
  const user = userEvent.setup();
  const first = show();
  await user.selectOptions(await screen.findByLabelText("Read operation"), "example.read");
  await user.click(screen.getByRole("button", { name: "Prepare review" }));
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  first.unmount();
  show();
  await user.click(await screen.findByRole("button", { name: "Retry the same proposal" }));
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toEqual(bodies[0]);
});

it("requires explicit rolling bounds and sends a separately reviewed window policy", async () => {
  sessionStorage.clear();
  let submitted: unknown;
  mockApi({
    "/api/heinzel/connected-read-options": () => [
      {
        capability: "example",
        method: "read",
        provider: "fixture",
        source: "fixture",
        input_schema: {},
        window_parameter: "period",
      },
    ],
    "/api/heinzel/connected-read-state": () => ({
      session_id: session,
      status: "none",
      proposal: null,
      descriptor: null,
    }),
    "/api/heinzel/propose-rolling-connected-read": async (request: Request) => {
      submitted = await request.clone().json();
      return proposal;
    },
  });
  const user = userEvent.setup();
  show();
  await user.selectOptions(await screen.findByLabelText("Read operation"), "example.read");
  await user.click(screen.getByRole("checkbox", { name: /Use a rolling window/ }));
  await user.type(screen.getByLabelText("Lookback seconds"), "60");
  await user.type(screen.getByLabelText("Lookahead seconds"), "600");
  await user.type(screen.getByLabelText("Display timezone (IANA)"), "Europe/Berlin");
  await user.click(screen.getByRole("button", { name: "Prepare review" }));
  expect(submitted).toMatchObject({
    session_id: session,
    fixed_arguments: {},
    window_policy: { parameter: "period", lookback_seconds: 60, lookahead_seconds: 600, timezone: "Europe/Berlin" },
  });
});

it("refuses an action whose provider candidate disclosure changed", async () => {
  const changed = { ...proposal, review: { ...proposal.review, candidate_read_disclosure: "Different data." } };
  const action = {
    id: "a1",
    module: "heinzel",
    operation: "approve-connected-read",
    status: "pending",
    command: "approve",
    arguments: { session_id: session, request_id: "r1", review: changed },
  };
  const chat = { id: "chat", personal_session_id: session, actions: [action], messages: [] };
  const { calls } = mockApi({
    "/api/heinzel/connected-read-options": () => [],
    "/api/heinzel/connected-read-state": () => ({
      session_id: session,
      status: "proposed",
      proposal,
      descriptor: null,
    }),
    "/api/heinzel/connected-read-review": () => proposal,
    "/api/system/runner/chat/propose-connected-read-review": () => chat,
    "/api/system/runner/chat/show": () => chat,
  });
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Review access" }));
  expect(screen.queryByRole("button", { name: /^confirm$/i })).not.toBeInTheDocument();
  expect(calls.some((r) => new URL(r.url).pathname.endsWith("/confirm"))).toBe(false);
});

it("reviews an inert chat source only after an explicit click and preserves its exact arguments", async () => {
  sessionStorage.clear();
  const suggestion = {
    session_id: session,
    request_id: "turn-1",
    revision: 1,
    based_on_revision: 0,
    capability: "example",
    method: "read",
    fixed_arguments: { ref: "https://example.invalid/a" },
    status: "suggested",
  };
  let submitted: unknown;
  const { calls } = mockApi({
    "/api/heinzel/latest-source-suggestion-view": () => suggestion,
    "/api/heinzel/connected-read-options": () => [
      { capability: "example", method: "read", provider: "fixture", source: "fixture", input_schema: {} },
    ],
    "/api/heinzel/connected-read-state": () => ({
      session_id: session,
      status: "none",
      proposal: null,
      descriptor: null,
    }),
    "/api/heinzel/prepare-suggested-source": async (request: Request) => {
      submitted = await request.clone().json();
      return proposal;
    },
  });
  const user = userEvent.setup();
  show();
  expect(await screen.findByText(/has not contacted a provider/)).toBeInTheDocument();
  expect(calls.some((r) => new URL(r.url).pathname.endsWith("/propose-connected-read"))).toBe(false);
  await user.click(screen.getByRole("button", { name: "Review source" }));
  expect(submitted).toMatchObject({ session_id: session, suggestion_request: "turn-1", suggestion_revision: 1 });
  expect(submitted).not.toHaveProperty("fixed_arguments");
  expect(calls.some((r) => new URL(r.url).pathname.endsWith("/confirm"))).toBe(false);
});

it("refuses review if the suggestion changed after it was displayed", async () => {
  sessionStorage.clear();
  const suggestion = {
    session_id: session,
    request_id: "turn-1",
    revision: 1,
    based_on_revision: 0,
    capability: "example",
    method: "read",
    fixed_arguments: { ref: "https://example.invalid/a" },
    status: "suggested",
  };
  let reads = 0;
  const { calls } = mockApi({
    "/api/heinzel/latest-source-suggestion-view": () => (++reads === 1 ? suggestion : { ...suggestion, revision: 2 }),
    "/api/heinzel/connected-read-options": () => [
      { capability: "example", method: "read", provider: "fixture", source: "fixture", input_schema: {} },
    ],
    "/api/heinzel/connected-read-state": () => ({
      session_id: session,
      status: "none",
      proposal: null,
      descriptor: null,
    }),
  });
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Review source" }));
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(calls.some((r) => new URL(r.url).pathname.endsWith("/prepare-suggested-source"))).toBe(false);
});
