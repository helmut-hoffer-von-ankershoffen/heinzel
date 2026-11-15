import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { HeinzelActions } from "./HeinzelActions";

const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const action = {
  id: "a1",
  tool: "system_bookmark_remove",
  module: "system.bookmark",
  operation: "remove",
  status: "pending",
  command: "hz system bookmark remove --entry b-1",
  arguments: { entry: "b-1" },
};
const chat = {
  id: "0123456789abcdef",
  personal_session_id: session,
  context: { kind: "heinzel", ref: session },
  actions: [action],
  messages: [],
};

function show() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <HeinzelActions sessionId={session} refresh="1" />
    </QueryClientProvider>,
  );
}

it("shows a Heinzel's proposal as a card and runs it only on Confirm", async () => {
  const { calls } = mockApi({
    "/api/system/runner/chat/heinzel-actions": () => [chat],
    "/api/system/runner/chat/confirm": () => ({ ...chat, actions: [{ ...action, status: "done" }] }),
  });
  const user = userEvent.setup();
  show();
  expect(await screen.findByText(action.command)).toBeInTheDocument();
  expect(calls.some((request) => new URL(request.url).pathname.endsWith("/confirm"))).toBe(false);
  await user.click(screen.getByRole("button", { name: /confirm/i }));
  const confirmed = calls.find((request) => new URL(request.url).pathname.endsWith("/confirm"));
  expect(new URL(confirmed?.url ?? "http://x").searchParams.get("session")).toBe(chat.id);
  expect(new URL(confirmed?.url ?? "http://x").searchParams.get("action")).toBe("a1");
});

it("never shows a chat that is bound to another Heinzel", async () => {
  mockApi({
    "/api/system/runner/chat/heinzel-actions": () => [
      { ...chat, personal_session_id: "hz-ZZZZZZZZZZZZZZZZZZZZZZZZZZ" },
    ],
  });
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load what this Heinzel proposed");
  expect(screen.queryByText(action.command)).not.toBeInTheDocument();
});

it("shows nothing when nothing waits", async () => {
  const { calls } = mockApi({
    "/api/system/runner/chat/heinzel-actions": () => [{ ...chat, actions: [{ ...action, status: "done" }] }],
  });
  const { container } = show();
  await waitFor(() => expect(calls.length).toBeGreaterThan(0));
  expect(container.querySelector("section")).toBeNull();
});
