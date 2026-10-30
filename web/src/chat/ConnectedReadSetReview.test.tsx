import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { ConnectedReadSetReview } from "@/chat/ConnectedReadSetReview";
import { DurableSession } from "@/chat/DurableSession";
import { mockApi } from "@/test/render";

function showSet() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConnectedReadSetReview sessionId={session} />
    </QueryClientProvider>,
  );
}

const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const entries = [
  { capability: "calendar", method: "events", fixed_arguments: { window: { since: "Monday", until: "Tuesday" } } },
  { capability: "documents", method: "read", fixed_arguments: { id: "fixture" } },
];
const suggestion = {
  session_id: session,
  request_id: "suggestion",
  revision: 1,
  based_on_revision: 0,
  entries,
  status: "suggested",
};
const proposal = {
  session,
  owner: "owner",
  request_id: "proposal",
  revision: 1,
  provenance: "owner-request",
  entries: entries.map((entry, i) => ({
    handle: `read-${i}`,
    terms: {
      ...entry,
      binding_sha256: "binding",
      review: {
        account_identity: `account-${i}`,
        source_revision: "source",
        provider_scopes: ["read"],
        fixed_arguments: entry.fixed_arguments,
      },
    },
  })),
};
const review = { request_id: "review", revision: 1, provenance: "host-observed", proposal };

it("keeps a suggested set inert until a person reviews and confirms the exact set", async () => {
  let saved: unknown = null;
  let suggested = false;
  let reviewRequest = "";
  let approved = false;
  let finishPrepare = () => {};
  const prepareGate = new Promise<void>((resolve) => {
    finishPrepare = resolve;
  });
  const action = () => ({
    id: "a1",
    module: "heinzel",
    operation: "approve-connected-read-set",
    status: approved ? "done" : "pending",
    command: "approve",
    arguments: { session_id: session, request_id: reviewRequest, review: { ...review, request_id: reviewRequest } },
  });
  const chat = () => ({
    id: "chat",
    personal_session_id: session,
    personal_owner_id: "owner",
    actions: [action()],
    messages: [],
  });
  const { calls } = mockApi({
    "/api/heinzel/connected-read-set-context": { session_id: session, owner: "owner" },
    "/api/heinzel/session": { session_id: session, name: "Fixture", messages: [] },
    "/api/heinzel/connected-read-set-proposal": () => saved,
    "/api/heinzel/latest-source-set-suggestion-view": () => (suggested ? suggestion : null),
    "/api/heinzel/chat": async (request: Request) => {
      const body = await request.json();
      return { session_id: session, request_id: body.request_id, disposition: "queued", inbox_position: 1 };
    },
    "/api/heinzel/answer-chat": async (request: Request) => {
      const body = await request.json();
      suggested = true;
      return {
        session_id: session,
        request_id: body.request_id,
        message: { role: "assistant", text: "Suggested sources." },
      };
    },
    "/api/heinzel/connected-read-options": () => entries.map((entry) => ({ ...entry, provider: "fixture" })),
    "/api/heinzel/propose-connected-read-set": async (request: Request) => {
      const body = await request.json();
      expect(body.selections).toEqual(entries);
      saved = proposal;
      return proposal;
    },
    "/api/heinzel/prepare-connected-read-set-review": async (request: Request) => {
      reviewRequest = (await request.json()).request_id;
      await prepareGate;
      return { ...review, request_id: reviewRequest };
    },
    "/api/heinzel/connected-read-set-review": () => ({ ...review, request_id: reviewRequest }),
    "/api/heinzel/connected-read-set-approval": () =>
      approved ? { ...review, request_id: reviewRequest } : { status: 409, body: {} },
    "/api/system/runner/chat/propose-connected-read-set-review": () => chat(),
    "/api/system/runner/chat/show": () => chat(),
    "/api/system/runner/chat/confirm": () => {
      approved = true;
      return chat();
    },
    "/api/heinzel/revoke-connected-read-set": () => {
      approved = false;
      return true;
    },
  });
  const user = userEvent.setup();
  const router = createMemoryRouter([
    { path: "/", element: <DurableSession sessionId={session} onClose={() => {}} /> },
  ]);
  render(
    <Providers client={queryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
  await user.click(await screen.findByRole("button", { name: "Ask about sources" }));
  const composer = screen.getByLabelText("Message your Heinzel");
  expect(composer).toHaveFocus();
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("/answer-chat"))).toBe(false);
  await user.type(composer, "Please suggest sources for my task.");
  await user.click(screen.getByRole("button", { name: "Queue message" }));
  await user.click(await screen.findByRole("button", { name: "Use suggested sources" }));
  expect(screen.getAllByText(/Window: Since: Monday · Until: Tuesday/).length).toBeGreaterThan(0);
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("/propose-connected-read-set"))).toBe(false);
  await user.click(screen.getByRole("button", { name: "Prepare set" }));
  expect(await screen.findByText(/Account: account-0/)).toBeInTheDocument();
  expect(screen.getByText(/Account: account-1/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Check current sources" }));
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("/connected-read-set-review"))).toBe(false);
  finishPrepare();
  await user.click(await screen.findByRole("button", { name: "Review set access" }));
  expect(await screen.findByText("Review these sources")).toBeInTheDocument();
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("/confirm"))).toBe(false);
  await user.click(screen.getByRole("button", { name: /confirm/i }));
  expect(await screen.findByText(/Confirmation recorded/)).toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "Revoke this set" }));
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("/revoke-connected-read-set"))).toBe(true);
});

it.each(["done", "uncertain", "failed"])(
  "recovers a lost confirmation as %s without repeating it",
  async (recovered) => {
    const action = {
      id: "a1",
      module: "heinzel",
      operation: "approve-connected-read-set",
      status: "pending",
      command: "approve",
      arguments: { session_id: session, request_id: "review", review },
    };
    let status = "pending";
    const chat = () => ({
      id: "chat",
      personal_session_id: session,
      personal_owner_id: "owner",
      actions: [{ ...action, status }],
      messages: [],
    });
    sessionStorage.setItem(`hz.connected-set:${session}:owner:review`, "review");
    sessionStorage.setItem(`hz.connected-set:${session}:owner:chat`, "chat");
    const { calls } = mockApi({
      "/api/heinzel/connected-read-set-context": { session_id: session, owner: "owner" },
      "/api/heinzel/session": { session_id: session, name: "Fixture", messages: [] },
      "/api/heinzel/connected-read-set-proposal": () => proposal,
      "/api/heinzel/latest-source-set-suggestion-view": () => null,
      "/api/heinzel/connected-read-options": () => [],
      "/api/heinzel/connected-read-set-review": () => review,
      "/api/heinzel/connected-read-set-approval": { status: 409, body: {} },
      "/api/system/runner/chat/show": () => chat(),
      "/api/system/runner/chat/recover": () => {
        status = recovered;
        return chat();
      },
      "/api/system/runner/chat/confirm": { status: 503, body: {} },
    });
    const makeRouter = () =>
      createMemoryRouter([{ path: "/", element: <DurableSession sessionId={session} onClose={() => {}} /> }]);
    const user = userEvent.setup();
    const view = render(
      <Providers client={queryClient()}>
        <RouterProvider router={makeRouter()} />
      </Providers>,
    );
    await user.click(await screen.findByRole("button", { name: /confirm/i }));
    expect(await screen.findByText(/decision outcome needs inspection/)).toBeInTheDocument();
    view.unmount();
    render(
      <Providers client={queryClient()}>
        <RouterProvider router={makeRouter()} />
      </Providers>,
    );
    expect(await screen.findByRole("button", { name: "Check decision outcome" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check decision outcome" }));
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    expect(calls.some((call) => new URL(call.url).pathname.endsWith("/recover"))).toBe(false);
    status = "running";
    await user.click(screen.getByRole("button", { name: "Check decision outcome" }));
    if (recovered === "done") expect(await screen.findByText(/Confirmation recorded/)).toBeInTheDocument();
    else expect(await screen.findByRole("button", { name: "Start a new review" })).toBeInTheDocument();
    expect(calls.filter((call) => new URL(call.url).pathname.endsWith("/recover"))).toHaveLength(1);
    expect(calls.filter((call) => new URL(call.url).pathname.endsWith("/confirm"))).toHaveLength(1);
    sessionStorage.clear();
  },
);

it("refuses a saved action whose reviewed account differs from the current review", async () => {
  sessionStorage.setItem(`hz.connected-set:${session}:owner:review`, "review");
  sessionStorage.setItem(`hz.connected-set:${session}:owner:chat`, "chat");
  const changed = structuredClone(review);
  const first = changed.proposal.entries[0];
  if (!first) throw new Error("Missing fixture entry");
  first.terms.review.account_identity = "different-account";
  const action = {
    id: "a1",
    module: "heinzel",
    operation: "approve-connected-read-set",
    status: "pending",
    command: "approve",
    arguments: { session_id: session, request_id: "review", review: changed },
  };
  const { calls } = mockApi({
    "/api/heinzel/connected-read-set-context": { session_id: session, owner: "owner" },
    "/api/heinzel/session": { session_id: session, name: "Fixture", messages: [] },
    "/api/heinzel/connected-read-set-proposal": () => proposal,
    "/api/heinzel/latest-source-set-suggestion-view": () => null,
    "/api/heinzel/connected-read-options": () => [],
    "/api/heinzel/connected-read-set-review": () => review,
    "/api/heinzel/connected-read-set-approval": { status: 409, body: {} },
    "/api/system/runner/chat/show": () => ({
      id: "chat",
      personal_session_id: session,
      personal_owner_id: "owner",
      actions: [action],
      messages: [],
    }),
  });
  const router = createMemoryRouter([
    { path: "/", element: <DurableSession sessionId={session} onClose={() => {}} /> },
  ]);
  render(
    <Providers client={queryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
  expect(await screen.findByText(/Sources ready for review/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("/confirm"))).toBe(false);
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

it.each(["pending", "missing", "changed"])(
  "keeps the decision fenced after a 200 response with %s action",
  async (outcome) => {
    const action = {
      id: "a1",
      module: "heinzel",
      operation: "approve-connected-read-set",
      status: "pending",
      command: "approve",
      arguments: { session_id: session, request_id: "review", review },
    };
    const chat = {
      id: "chat",
      personal_session_id: session,
      personal_owner_id: "owner",
      actions: [action],
      messages: [],
    };
    sessionStorage.setItem(`hz.connected-set:${session}:owner:review`, "review");
    sessionStorage.setItem(`hz.connected-set:${session}:owner:chat`, "chat");
    const returned =
      outcome === "missing"
        ? []
        : outcome === "changed"
          ? [{ ...action, arguments: { ...action.arguments, request_id: "other" }, status: "done" }]
          : [action];
    const { calls } = mockApi({
      "/api/heinzel/connected-read-set-context": { session_id: session, owner: "owner" },
      "/api/heinzel/connected-read-set-proposal": proposal,
      "/api/heinzel/latest-source-set-suggestion-view": null,
      "/api/heinzel/connected-read-options": [],
      "/api/heinzel/connected-read-set-review": review,
      "/api/heinzel/connected-read-set-approval": { status: 409, body: {} },
      "/api/system/runner/chat/show": chat,
      "/api/system/runner/chat/confirm": { ...chat, actions: returned },
    });
    const user = userEvent.setup();
    const view = showSet();
    await user.click(await screen.findByRole("button", { name: /confirm/i }));
    expect(await screen.findByText(/decision outcome needs inspection/)).toBeInTheDocument();
    view.unmount();
    showSet();
    expect(await screen.findByText(/decision outcome needs inspection/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    expect(calls.filter((call) => new URL(call.url).pathname.endsWith("/confirm"))).toHaveLength(1);
  },
);

it.each(["throw", "drop"])(
  "retains a pending selection and sends no proposal when storage %s occurs",
  async (failure) => {
    const { calls } = mockApi({
      "/api/heinzel/connected-read-set-context": { session_id: session, owner: "owner" },
      "/api/heinzel/connected-read-set-proposal": null,
      "/api/heinzel/latest-source-set-suggestion-view": suggestion,
      "/api/heinzel/connected-read-options": entries.map((entry) => ({ ...entry, provider: "fixture" })),
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      if (failure === "throw") throw new Error("storage denied");
    });
    const user = userEvent.setup();
    showSet();
    await user.click(await screen.findByRole("button", { name: "Use suggested sources" }));
    await user.click(screen.getByRole("button", { name: "Prepare set" }));
    expect(await screen.findByRole("button", { name: "Retry the same proposal" })).toBeInTheDocument();
    expect(calls.some((call) => new URL(call.url).pathname.endsWith("/propose-connected-read-set"))).toBe(false);
  },
);

it.each(["throw", "drop"])("does not send a decision when its recovery fence storage %s occurs", async (failure) => {
  const action = {
    id: "a1",
    module: "heinzel",
    operation: "approve-connected-read-set",
    status: "pending",
    command: "approve",
    arguments: { session_id: session, request_id: "review", review },
  };
  sessionStorage.setItem(`hz.connected-set:${session}:owner:review`, "review");
  sessionStorage.setItem(`hz.connected-set:${session}:owner:chat`, "chat");
  const { calls } = mockApi({
    "/api/heinzel/connected-read-set-context": { session_id: session, owner: "owner" },
    "/api/heinzel/connected-read-set-proposal": proposal,
    "/api/heinzel/latest-source-set-suggestion-view": null,
    "/api/heinzel/connected-read-options": [],
    "/api/heinzel/connected-read-set-review": review,
    "/api/heinzel/connected-read-set-approval": { status: 409, body: {} },
    "/api/system/runner/chat/show": {
      id: "chat",
      personal_session_id: session,
      personal_owner_id: "owner",
      actions: [action],
      messages: [],
    },
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    if (failure === "throw") throw new Error("storage denied");
  });
  const user = userEvent.setup();
  showSet();
  await user.click(await screen.findByRole("button", { name: /confirm/i }));
  expect(await screen.findByText(/could not be verified/)).toBeInTheDocument();
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("/confirm"))).toBe(false);
});

it("ignores another owner's persisted review and handles denied storage reads", async () => {
  sessionStorage.setItem(`hz.connected-set:${session}:other:chat`, "foreign-chat");
  sessionStorage.setItem(`hz.connected-set:${session}:other:review`, "foreign-review");
  const { calls } = mockApi({
    "/api/heinzel/connected-read-set-context": { session_id: session, owner: "owner" },
    "/api/heinzel/connected-read-set-proposal": null,
    "/api/heinzel/latest-source-set-suggestion-view": null,
    "/api/heinzel/connected-read-options": [],
  });
  const view = showSet();
  expect(await screen.findByRole("region", { name: "Sources for this chat" })).toBeInTheDocument();
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("/show"))).toBe(false);
  view.unmount();
  const getItem = Storage.prototype.getItem;
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key) {
    if (key.startsWith("hz.connected-set:")) throw new Error("storage denied");
    return getItem.call(this, key);
  });
  showSet();
  expect(await screen.findByText(/Review state could not be loaded/)).toBeInTheDocument();
});

it("hides cached prior-owner data while the session owner is reverified", async () => {
  let owner = "owner";
  let finishContext = () => {};
  let block = false;
  const gate = new Promise<void>((resolve) => {
    finishContext = resolve;
  });
  mockApi({
    "/api/heinzel/connected-read-set-context": async () => {
      if (block) await gate;
      return { session_id: session, owner };
    },
    "/api/heinzel/connected-read-set-proposal": () => (owner === "owner" ? proposal : null),
    "/api/heinzel/latest-source-set-suggestion-view": null,
    "/api/heinzel/connected-read-options": [],
    "/api/heinzel/connected-read-set-approval": { status: 409, body: {} },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ConnectedReadSetReview sessionId={session} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/Account: account-0/)).toBeInTheDocument();
  block = true;
  owner = "other-owner";
  const pending = client.invalidateQueries({ queryKey: ["heinzel", "connected-read-set-context", session] });
  await waitFor(() => expect(screen.queryByText(/Account: account-0/)).not.toBeInTheDocument());
  finishContext();
  await pending;
  expect(await screen.findByRole("region", { name: "Sources for this chat" })).toBeInTheDocument();
  expect(screen.queryByText(/Account: account-0/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
});
