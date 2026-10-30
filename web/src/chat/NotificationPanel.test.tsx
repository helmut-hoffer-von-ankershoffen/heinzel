import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { NotificationPanel } from "./NotificationPanel";

const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const draft = {
  session_id: session,
  request_id: "notice-one",
  topic: "example",
  title: "Review needed",
  body: "A fictional draft",
  status: "proposed",
  delivery: "local-notification",
  disclosure: "The title and body may appear on this device; queued does not mean shown.",
};
function show() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <NotificationPanel sessionId={session} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("shows exact private terms and queues only after explicit action confirmation", async () => {
  const action = {
    id: "a1",
    module: "heinzel",
    operation: "queue-agent-notification",
    status: "pending",
    command: "Review notification",
    arguments: { session_id: session, request_id: "notice-one", review: draft },
  };
  const chat = { id: "chat", personal_session_id: session, actions: [action], messages: [] };
  const { calls } = mockApi({
    "/api/heinzel/notification-proposals": () => ({
      session_id: session,
      items: [{ request_id: "notice-one", seq: 1 }],
      next_cursor: null,
    }),
    "/api/heinzel/notification-proposal": () => ({ body: draft }),
    "/api/system/runner/chat/propose-agent-notification": () => chat,
    "/api/system/runner/chat/show": () => chat,
    "/api/system/runner/chat/confirm": () => ({ ...chat, actions: [{ ...action, status: "done" }] }),
  });
  const user = userEvent.setup();
  show();
  expect(await screen.findByText("A fictional draft")).toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "Review notification" }));
  expect(await screen.findByText("Review notification", { selector: "h3" })).toBeInTheDocument();
  expect(calls.some((request) => new URL(request.url).pathname.endsWith("/confirm"))).toBe(false);
  await user.click(screen.getByRole("button", { name: /confirm/i }));
  expect(await screen.findByText("Queued under local policy; display is not confirmed.")).toBeInTheDocument();
});

it("fails closed for a foreign draft and never stages an action", async () => {
  const { calls } = mockApi({
    "/api/heinzel/notification-proposals": () => ({
      session_id: session,
      items: [{ request_id: "notice-one", seq: 1 }],
      next_cursor: null,
    }),
    "/api/heinzel/notification-proposal": () => ({ body: { ...draft, session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW" } }),
  });
  show();
  expect(await screen.findByText("This notification draft could not be verified.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review notification" })).not.toBeInTheDocument();
  expect(calls.some((request) => new URL(request.url).pathname.includes("propose-agent-notification"))).toBe(false);
});

it("inspects an uncertain outcome without retrying or falsely recovering the action", async () => {
  const action = {
    id: "a1",
    module: "heinzel",
    operation: "queue-agent-notification",
    status: "uncertain",
    command: "Review notification",
    arguments: { session_id: session, request_id: "notice-one", review: draft },
  };
  const { calls } = mockApi({
    "/api/heinzel/notification-proposals": () => ({
      session_id: session,
      items: [{ request_id: "notice-one", seq: 1 }],
      next_cursor: null,
    }),
    "/api/heinzel/notification-proposal": () => ({ body: draft }),
    "/api/system/runner/chat/show": () => ({
      id: "chat",
      personal_session_id: session,
      actions: [action],
      messages: [],
    }),
    "/api/heinzel/notification-outcome": () => "uncertain",
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/?notification-chat=chat"]}>
        <NotificationPanel sessionId={session} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await user.click(await screen.findByRole("button", { name: "Check notification outcome" }));
  expect(await screen.findByText("Still uncertain; no notification was repeated.")).toBeInTheDocument();
  expect(calls.some((request) => /\/(confirm|recover)$/.test(new URL(request.url).pathname))).toBe(false);
});
