import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { SchemaChatAction } from "@/api/client";
import { CapturedWriteReview } from "./CapturedWriteReview";

const review = {
  owner: "owner",
  session: "session",
  request_id: "draft",
  provider: "fake",
  binding_id: "binding",
  account: "account-one",
  source: "source-one",
  reference: "fixture://document/one",
  operation: "replace_section",
  section: "summary",
  base_revision: "one",
  write_scope: "fixture-write",
  content: "exact replacement",
};
const action: SchemaChatAction = {
  id: "action",
  module: "heinzel",
  operation: "execute-captured-write",
  status: "pending",
  command: "Review",
  tool: "heinzel_execute_captured_write",
  proposed: "2026-01-01T00:00:00Z",
  result: "",
  mcp_authorized: false,
  arguments: { session_id: "session", request_id: "draft", review },
};
const show = (value = action, onConfirm = vi.fn(async () => {})) =>
  render(<CapturedWriteReview action={value} sessionId="session" chatId="chat" onConfirm={onConfirm} />);
beforeEach(() => sessionStorage.clear());

it("shows exact terms and persists a fence before confirming", async () => {
  const confirm = vi.fn(async () => {
    expect(sessionStorage.getItem("captured-write:session:chat:action")).toBe("attempted");
  });
  const view = show(action, confirm);
  for (const text of [
    "account-one",
    "source-one",
    "fixture://document/one",
    "one",
    "fixture-write",
    "exact replacement",
  ])
    expect(
      screen.getByText((_, element) => element?.tagName === "PRE" && !!element.textContent?.includes(text)),
    ).toBeTruthy();
  expect(screen.getByText(/Current section text is not shown/)).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Confirm replacement" }));
  expect(confirm).toHaveBeenCalledTimes(1);
  view.unmount();
  show(action, confirm);
  expect(screen.queryByRole("button", { name: "Confirm replacement" })).toBeNull();
  expect(confirm).toHaveBeenCalledTimes(1);
});

it("refuses a changed session or extra confirmation arguments", () => {
  const { rerender } = show({ ...action, arguments: { ...action.arguments, session_id: "foreign" } });
  expect(screen.queryByRole("region", { name: "Captured write review" })).toBeNull();
  rerender(
    <CapturedWriteReview
      action={{ ...action, arguments: { ...action.arguments, approval_proof: "fake" } }}
      sessionId="session"
      chatId="chat"
      onConfirm={async () => {}}
    />,
  );
  expect(screen.queryByRole("button", { name: "Confirm replacement" })).toBeNull();
});

it("shows a recorded acknowledgement without offering another confirmation", () => {
  show({ ...action, status: "done" });
  expect(screen.getByText(/confirmed attempt was acknowledged/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Confirm replacement" })).toBeNull();
});

it("fails closed when the confirmation fence cannot be saved", async () => {
  const confirm = vi.fn(async () => {});
  const storage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  try {
    show(action, confirm);
    await userEvent.click(screen.getByRole("button", { name: "Confirm replacement" }));
    expect(confirm).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Confirm replacement" })).toBeNull();
  } finally {
    storage.mockRestore();
  }
});

it("admits only one click before React rerenders", () => {
  const confirm = vi.fn(() => new Promise<void>(() => {}));
  show(action, confirm);
  const button = screen.getByRole("button", { name: "Confirm replacement" });
  act(() => {
    fireEvent.click(button);
    fireEvent.click(button);
  });
  expect(confirm).toHaveBeenCalledTimes(1);
});
