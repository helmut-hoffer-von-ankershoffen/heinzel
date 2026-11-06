import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { mockApi } from "@/test/render";
import { CapturedWriteActivity } from "./CapturedWriteActivity";

const path = "/api/heinzel/captured-write-activity-page";
const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";

function renderPanel(onReview = vi.fn()) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <CapturedWriteActivity sessionId={sessionId} onReview={onReview} />
    </QueryClientProvider>,
  );
}

it("reads pages only when opened and shows a set-aside draft and recorded acknowledgement", async () => {
  const { calls } = mockApi({
    [path]: (request: Request) => {
      const query = new URL(request.url).searchParams;
      expect(query.get("session_id")).toBe(sessionId);
      const older = query.get("before") === "10";
      return {
        session_id: sessionId,
        next_before: older ? null : 10,
        items: [
          {
            request_id: older ? "old" : "recent",
            status: older ? "acknowledged" : "discarded",
            reference: older ? "Older draft" : "Set-aside draft",
            revision: older ? "opaque-revision" : null,
          },
        ],
      };
    },
  });
  const user = userEvent.setup();
  renderPanel();
  expect(calls).toHaveLength(0);
  await user.click(screen.getByText("Document write activity"));
  expect(await screen.findByText("Set aside")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Older" }));
  expect(await screen.findByText("Provider acknowledged the write")).toBeVisible();
  expect(screen.getByText("Recorded revision: opaque-revision")).toBeVisible();
  expect(calls.map((request) => request.method)).toEqual(["GET", "GET"]);
});

it("refuses to display a response bound to a different conversation", async () => {
  mockApi({
    [path]: { session_id: "another", items: [{ request_id: "private", reference: "Other conversation" }] },
  });
  renderPanel();
  await userEvent.setup().click(screen.getByText("Document write activity"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t check write activity");
  expect(screen.queryByText("Other conversation")).not.toBeInTheDocument();
});

it("hands the exact scoped action to the conversation without confirming it", async () => {
  const { calls } = mockApi({
    [path]: { session_id: sessionId, items: [{ request_id: "draft", status: "draft", reference: "Draft" }] },
    "/api/system/runner/chat/propose-captured-write": {
      id: "chat",
      personal_session_id: sessionId,
      actions: [
        {
          id: "action",
          module: "heinzel",
          operation: "execute-captured-write",
          status: "pending",
          command: "Review the exact destination",
          arguments: { session_id: sessionId, request_id: "draft", review: { content: "Exact text" } },
        },
      ],
    },
  });
  const onReview = vi.fn();
  const user = userEvent.setup();
  renderPanel(onReview);
  await user.click(screen.getByText("Document write activity"));
  await user.click(await screen.findByRole("button", { name: "Review write" }));
  expect(onReview).toHaveBeenCalledWith(expect.objectContaining({ id: "chat", personal_session_id: sessionId }));
  expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  expect(calls.map((request) => new URL(request.url).pathname)).toEqual([
    path,
    "/api/system/runner/chat/propose-captured-write",
  ]);
});
