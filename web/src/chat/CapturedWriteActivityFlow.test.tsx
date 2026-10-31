import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { Providers, queryClient, routes } from "@/App";
import { chatKey } from "@/api/chat";
import * as tokenSource from "@/api/token";
import { shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";

const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const chatId = "0123456789abcdef";
const review = {
  owner: "owner",
  session,
  request_id: "draft-one",
  provider: "fixture",
  binding_id: "binding",
  account: "account-one",
  source: "source-one",
  reference: "fixture://document/one",
  operation: "replace_section",
  section: "summary",
  base_revision: "one",
  write_scope: "fixture-write",
  content: "replacement",
};
const action = {
  id: "a1",
  module: "heinzel",
  operation: "execute-captured-write",
  status: "pending",
  command: "Review",
  tool: "heinzel_execute_captured_write",
  proposed: "2026-01-01T00:00:00Z",
  result: "",
  mcp_authorized: false,
  arguments: { session_id: session, request_id: "draft-one", review },
};
const chat = {
  id: chatId,
  personal_session_id: session,
  title: "Write review",
  messages: [],
  actions: [action],
  costs: { spent_usd: 0, session_max_usd: 2, message_max_usd: 0.2 },
};
afterEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
});
it.each(["acknowledged", "uncertain"])("refreshes actual chat activity after a %s confirmation", async (outcome) => {
  let status = "draft";
  const { calls } = mockApi({
    ...shell,
    "/api/heinzel/session": () => ({ session_id: session, name: "Personal helper", messages: [] }),
    "/api/heinzel/personal-artifacts": () => ({ session_id: session, artifacts: [] }),
    "/api/heinzel/captured-write-activity-page": () => ({
      session_id: session,
      items: [
        {
          request_id: "draft-one",
          seq: 3,
          at: "2026-05-04T12:00:00Z",
          provider: "fixture",
          account: "account-one",
          source: "source-one",
          reference: "fixture://document/one",
          status,
          revision: status === "acknowledged" ? "two" : null,
        },
      ],
      next_before: null,
    }),
    "/api/system/runner/chat/show": () => chat,
    "/api/system/runner/chat/confirm": () => {
      status = outcome;
      return { ...chat, actions: [{ ...action, status: outcome === "acknowledged" ? "done" : "uncertain" }] };
    },
  });
  renderAt(`/heinzel?chat=${session}&agent-chat=${chatId}`);
  const activity = await screen.findByRole("region", { name: "Recorded artifact write activity" });
  expect(await within(activity).findByText("Draft; no confirmed attempt")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Confirm replacement" }));
  const expected =
    outcome === "acknowledged" ? "Provider acknowledged the attempt" : "Confirmed attempt; outcome unknown";
  expect(await screen.findByText(expected)).toBeInTheDocument();
  expect(
    calls.filter((request) => request.method === "POST" && new URL(request.url).pathname.endsWith("/confirm")),
  ).toHaveLength(1);
});

it("does not cache a delayed confirmation after the tab credential changes", async () => {
  const tokenReads = vi.spyOn(tokenSource, "token");
  let release: (value: unknown) => void = () => {
    throw new Error("not requested");
  };
  const pending = new Promise<unknown>((resolve) => {
    release = resolve;
  });
  const { calls } = mockApi({
    ...shell,
    "/api/heinzel/session": () => ({ session_id: session, name: "Personal helper", messages: [] }),
    "/api/heinzel/personal-artifacts": () => ({ session_id: session, artifacts: [] }),
    "/api/heinzel/captured-write-activity-page": () => ({ session_id: session, items: [], next_before: null }),
    "/api/system/runner/chat/show": () => chat,
    "/api/system/runner/chat/confirm": () => pending,
  });
  sessionStorage.setItem("hz.token", "old-owner-token");
  const client = queryClient();
  const router = createMemoryRouter(routes, { initialEntries: [`/heinzel?chat=${session}&agent-chat=${chatId}`] });
  render(
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>,
  );
  await screen.findByText("No recorded write activity on this page.");
  fireEvent.click(await screen.findByRole("button", { name: "Confirm replacement" }));
  await waitFor(() =>
    expect(calls.filter((request) => new URL(request.url).pathname.endsWith("/confirm"))).toHaveLength(1),
  );
  const readsBeforeResponse = tokenReads.mock.calls.length;
  sessionStorage.setItem("hz.token", "new-owner-token");
  await act(async () => {
    release({ ...chat, actions: [{ ...action, status: "done", result: "OLD PRIVATE RESULT" }] });
    await pending;
  });
  await waitFor(() => expect(tokenReads.mock.calls.length).toBeGreaterThan(readsBeforeResponse));
  expect(client.getQueryData<typeof chat>(chatKey(chatId))?.actions[0]?.status).toBe("pending");
  expect(screen.queryByText(/confirmed attempt was acknowledged/)).not.toBeInTheDocument();
  expect(calls.filter((request) => new URL(request.url).pathname.endsWith("/confirm"))).toHaveLength(1);
});

it("does not recover or refresh an old request after its credential changes and the request fails", async () => {
  const tokenReads = vi.spyOn(tokenSource, "token");
  let rejectRequest: (reason: Error) => void = () => {
    throw new Error("not requested");
  };
  const pending = new Promise<never>((_resolve, reject) => {
    rejectRequest = reject;
  });
  const { calls } = mockApi({
    ...shell,
    "/api/heinzel/session": () => ({ session_id: session, name: "Personal helper", messages: [] }),
    "/api/heinzel/personal-artifacts": () => ({ session_id: session, artifacts: [] }),
    "/api/heinzel/captured-write-activity-page": () => ({ session_id: session, items: [], next_before: null }),
    "/api/system/runner/chat/show": () => chat,
    "/api/system/runner/chat/confirm": () => pending,
  });
  sessionStorage.setItem("hz.token", "old-owner-token");
  const client = queryClient();
  const router = createMemoryRouter(routes, { initialEntries: [`/heinzel?chat=${session}&agent-chat=${chatId}`] });
  render(
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>,
  );
  await screen.findByText("No recorded write activity on this page.");
  fireEvent.click(await screen.findByRole("button", { name: "Confirm replacement" }));
  await waitFor(() =>
    expect(calls.filter((request) => new URL(request.url).pathname.endsWith("/confirm"))).toHaveLength(1),
  );
  const readsBeforeResponse = tokenReads.mock.calls.length;
  const initialShow = calls.filter((request) => new URL(request.url).pathname.endsWith("/chat/show")).length;
  const initialActivity = calls.filter((request) =>
    new URL(request.url).pathname.endsWith("/captured-write-activity-page"),
  ).length;
  sessionStorage.setItem("hz.token", "new-owner-token");
  await act(async () => {
    rejectRequest(new Error("disconnected"));
    await pending.catch(() => {});
  });
  await waitFor(() => expect(tokenReads.mock.calls.length).toBeGreaterThan(readsBeforeResponse));
  expect(calls.filter((request) => new URL(request.url).pathname.endsWith("/chat/show"))).toHaveLength(initialShow);
  expect(
    calls.filter((request) => new URL(request.url).pathname.endsWith("/captured-write-activity-page")),
  ).toHaveLength(initialActivity);
  expect(client.getQueryData<typeof chat>(chatKey(chatId))?.actions[0]?.status).toBe("pending");
});

it("does not cache a delayed confirmation after switching personal sessions", async () => {
  const tokenReads = vi.spyOn(tokenSource, "token");
  const other = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
  let release: (value: unknown) => void = () => {
    throw new Error("not requested");
  };
  const pending = new Promise<unknown>((resolve) => {
    release = resolve;
  });
  mockApi({
    ...shell,
    "/api/heinzel/session": (request: Request) => ({
      session_id: new URL(request.url).searchParams.get("session_id"),
      name: "Personal helper",
      messages: [],
    }),
    "/api/heinzel/personal-artifacts": (request: Request) => ({
      session_id: new URL(request.url).searchParams.get("session_id"),
      artifacts: [],
    }),
    "/api/heinzel/captured-write-activity-page": (request: Request) => ({
      session_id: new URL(request.url).searchParams.get("session_id"),
      items: [],
      next_before: null,
    }),
    "/api/system/runner/chat/show": () => chat,
    "/api/system/runner/chat/confirm": () => pending,
  });
  sessionStorage.setItem("hz.token", "owner-token");
  const client = queryClient();
  const router = createMemoryRouter(routes, { initialEntries: [`/heinzel?chat=${session}&agent-chat=${chatId}`] });
  render(
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Confirm replacement" }));
  await act(async () => {
    await router.navigate(`/heinzel?chat=${other}`);
  });
  await screen.findByText("No recorded write activity on this page.");
  const readsBeforeResponse = tokenReads.mock.calls.length;
  await act(async () => {
    release({ ...chat, actions: [{ ...action, status: "done", result: "OLD PRIVATE RESULT" }] });
    await pending;
  });
  await waitFor(() => expect(tokenReads.mock.calls.length).toBeGreaterThan(readsBeforeResponse));
  expect(client.getQueryData<typeof chat>(chatKey(chatId))?.actions[0]?.status).toBe("pending");
  expect(screen.queryByText(/confirmed attempt was acknowledged/)).not.toBeInTheDocument();
});
