import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router";
import { expect, it, vi } from "vitest";
import { config, shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";
import { StartHelper } from "./StartHelper";

function show() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <StartHelper />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
async function fill(root: Pick<typeof screen, "getByText" | "getByLabelText"> = screen) {
  await userEvent.click(root.getByText("Start a helper from your first message"));
  await userEvent.type(root.getByLabelText("Helper name"), "Private helper");
  await userEvent.type(root.getByLabelText("First message"), "Private first message");
}

it("sends one named first message and opens its durable chat with separate review sections", async () => {
  const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
  const { calls } = mockApi({
    ...shell,
    "/api/system/config/show": {
      ...config,
      settings: [
        ...config.settings,
        { section: "hz", key: "features", value: ["connected_frontdoor"], description: "", env: "HZ_FEATURES" },
      ],
    },
    "/api/heinzel/sessions": { sessions: [], next: [] },
    "/api/heinzel/start-helper": async (request: Request) => {
      const body = await request.clone().json();
      return { session_id: sessionId, request_id: body.request_id, message: {} };
    },
    "/api/heinzel/session": (request: Request) => {
      expect(new URL(request.url).searchParams.get("session_id")).toBe(sessionId);
      return {
        session_id: sessionId,
        name: "Private helper",
        messages: [{ id: "first", seq: 1, role: "user", text: "Private first message" }],
      };
    },
  });
  renderAt("/heinzel");
  await screen.findByText("Start a helper from your first message");
  await fill();
  expect(calls.filter((request) => request.method === "POST")).toEqual([]);
  await userEvent.click(screen.getByRole("button", { name: "Send first message" }));
  const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
  expect(await chat.findByText("Private first message")).toBeInTheDocument();
  const review = within(chat.getByRole("region", { name: "Review your helper" }));
  expect(review.getByText("1. Agent terms")).toBeInTheDocument();
  expect(review.getByText("2. Source access and time window")).toBeInTheDocument();
  expect(review.getByText("3. Recurring turns and local notices")).toBeInTheDocument();
  const writes = calls.filter((request) => request.method === "POST");
  expect(writes.map((request) => new URL(request.url).pathname)).toEqual(["/api/heinzel/start-helper"]);
  expect(await writes[0]?.clone().json()).toEqual({
    request_id: expect.any(String),
    name: "Private helper",
    prompt: "Private first message",
  });
});

it("does not mount the first-prompt form or grouped review without its preview feature", async () => {
  const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
  mockApi({
    ...shell,
    "/api/heinzel/sessions": { sessions: [], next: [] },
    "/api/heinzel/session": () => ({
      session_id: sessionId,
      name: "Existing helper",
      messages: [{ id: "first", seq: 1, role: "user", text: "Existing message" }],
    }),
  });
  renderAt(`/heinzel?chat=${sessionId}`);
  await screen.findByText("Existing message");
  expect(screen.queryByText("Start a helper from your first message")).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Review your helper" })).not.toBeInTheDocument();
  expect(screen.getByText("Scheduled Heinzel terms")).toBeInTheDocument();
});

it("lets the accepted submission finish without reopening its chat after the form unmounts", async () => {
  const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
  const waiting: Array<() => void> = [];
  const { calls } = mockApi({
    "/api/heinzel/start-helper": async (request: Request) => {
      const body = await request.clone().json();
      return new Promise((resolve) => {
        waiting.push(() => resolve({ session_id: sessionId, request_id: body.request_id, message: {} }));
      });
    },
  });
  const client = new QueryClient();
  const invalidation = vi.spyOn(client, "invalidateQueries");
  const router = createMemoryRouter(
    [
      { path: "/heinzel", element: <StartHelper /> },
      { path: "/elsewhere", element: <p>Unrelated page</p> },
    ],
    { initialEntries: ["/heinzel"] },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await fill();
  await userEvent.click(screen.getByRole("button", { name: "Send first message" }));
  await waitFor(() => expect(waiting).toHaveLength(1));
  await act(async () => {
    await router.navigate("/elsewhere");
  });
  expect(screen.getByText("Unrelated page")).toBeInTheDocument();
  await act(async () => {
    waiting[0]?.();
  });
  expect(invalidation).toHaveBeenCalledWith({ queryKey: ["heinzel", "sessions"] });
  expect(router.state.location.pathname).toBe("/elsewhere");
  expect(router.state.location.search).toBe("");
  expect(calls).toHaveLength(1);
});

it.each(["owner", "profile"])(
  "keeps uncertain content out of storage and refuses replay after a %s switch",
  async (changed) => {
    sessionStorage.setItem("hz.token", "before-switch");
    const { calls } = mockApi({ "/api/heinzel/start-helper": { status: 503, body: { error: "uncertain" } } });
    const first = show();
    await fill();
    expect(calls).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Send first message" }));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("First message")).toHaveValue("");
    expect(screen.getByLabelText("Helper name")).toHaveValue("");
    expect(screen.getByRole("button", { name: "First message submitted" })).toBeDisabled();
    expect(localStorage.getItem("hz.start-helper.pending")).toBeNull();
    expect([...Object.values(localStorage), ...Object.values(sessionStorage)].join("")).not.toContain("Private");
    first.unmount(); // The server now represents a different owner/profile on this same origin.
    if (changed === "owner") sessionStorage.setItem("hz.token", "after-switch");
    show();
    await userEvent.click(screen.getByText("Start a helper from your first message"));
    expect(screen.getByLabelText("First message")).toHaveValue("");
    expect(screen.getByLabelText("Helper name")).toHaveValue("");
    expect(screen.queryByText(/Submitted request reference/)).not.toBeInTheDocument();
    expect(calls).toHaveLength(1);
    expect(calls[0]?.headers.get("Authorization")).toBe("Bearer before-switch");
    expect(screen.getByText(/Reload loses unsent text/)).toBeInTheDocument();
  },
);

it("never displays, sends or deletes an unbound earlier owner's stored request", async () => {
  const evidence = JSON.stringify({
    request_id: "earlier",
    name: "Previous private name",
    prompt: "Previous private prompt",
  });
  localStorage.setItem("hz.start-helper.pending", evidence);
  const { calls } = mockApi({});
  show();
  await userEvent.click(screen.getByText("Start a helper from your first message"));
  expect(screen.getByRole("button")).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("unbound request");
  expect(document.body.textContent).not.toContain("Previous private");
  expect(screen.getByLabelText("First message")).toHaveValue("");
  expect(calls).toHaveLength(0);
  expect(localStorage.getItem("hz.start-helper.pending")).toBe(evidence);
});

it("isolates concurrent mounted tabs and never clears another tab's unresolved evidence", async () => {
  const waiting: Array<{ body: Record<string, unknown>; finish: (value: unknown) => void }> = [];
  mockApi({
    "/api/heinzel/start-helper": async (request: Request) =>
      new Promise((finish) => {
        void request.json().then((body) => waiting.push({ body, finish }));
      }),
  });
  const first = show();
  const second = show();
  const one = within(first.container);
  const two = within(second.container);
  await fill(one);
  await fill(two);
  await userEvent.click(one.getByRole("button", { name: "Send first message" }));
  await userEvent.click(two.getByRole("button", { name: "Send first message" }));
  await waitFor(() => expect(waiting).toHaveLength(2));
  const [oneRequest, twoRequest] = waiting;
  if (!oneRequest || !twoRequest) throw new Error("Both requests must be pending");
  expect(oneRequest.body.request_id).not.toBe(twoRequest.body.request_id);
  const evidence = JSON.stringify({ request_id: "another-tab", prompt: "Unresolved evidence" });
  localStorage.setItem("hz.start-helper.pending", evidence);
  oneRequest.finish({
    session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV",
    request_id: oneRequest.body.request_id,
    message: {},
  });
  twoRequest.finish({ status: 503, body: { error: "uncertain" } });
  await two.findByRole("alert");
  expect(localStorage.getItem("hz.start-helper.pending")).toBe(evidence);
  expect(two.getByRole("button", { name: "First message submitted" })).toBeDisabled();
  expect(two.getByLabelText("First message")).toHaveValue("");
});
