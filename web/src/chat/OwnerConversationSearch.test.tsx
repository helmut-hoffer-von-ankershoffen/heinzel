import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { mockApi } from "@/test/render";
import { OwnerConversationSearch } from "./OwnerConversationSearch";

it("continues bounded progress across agents and opens the correct message", async () => {
  const { calls } = mockApi({
    "/api/heinzel/search-agent-conversations": async (request: Request) => {
      const { request: body } = await request.clone().json();
      if (!body.cursor)
        return {
          status: "indexing",
          session_id: "agent-one",
          items: [],
          jump_cursors: [],
          next_cursor: "one",
          completeness: "unknown",
        };
      const second = body.cursor === "two";
      return {
        status: second ? "done" : "results",
        session_id: second ? "agent-two" : "agent-one",
        items: [{ seq: 2, text: second ? "Second match" : "First match", at: "2026-05-04T12:00:00Z" }],
        jump_cursors: [second ? "jump-two" : "jump-one"],
        next_cursor: second ? null : "two",
        completeness: "unknown",
      };
    },
  });
  const open = vi.fn();
  const user = userEvent.setup();
  render(<OwnerConversationSearch onJump={open} />);
  await user.click(screen.getByText("Search all Heinzel conversations"));
  await user.type(screen.getByLabelText("Text"), "needle");
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByText("First match")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Continue search" }));
  expect(await screen.findByText("Second match")).toBeInTheDocument();
  expect(screen.queryByText("First match")).not.toBeInTheDocument();
  const second = screen.getByRole("button", { name: "Open message" });
  const firstCall = calls[0];
  if (!second || !firstCall) throw new Error("Missing result or request");
  await user.click(second);
  expect(open).toHaveBeenCalledWith("agent-two", "jump-two");
  expect(calls).toHaveLength(3);
  expect(new URL(firstCall.url).search).toBe("");
});

it("discards a late prior query response when the text changes", async () => {
  let resolveOld!: (value: unknown) => void;
  const old = new Promise((resolve) => {
    resolveOld = resolve;
  });
  mockApi({
    "/api/heinzel/search-agent-conversations": async (request: Request) => {
      const { request: body } = await request.clone().json();
      if (body.query === "old") return old;
      return {
        status: "done",
        session_id: "new-agent",
        items: [{ seq: 1, text: "New result", at: "2026-05-04T12:00:00Z" }],
        jump_cursors: ["new"],
        next_cursor: null,
        completeness: "unknown",
      };
    },
  });
  const user = userEvent.setup();
  render(<OwnerConversationSearch onJump={vi.fn()} />);
  await user.click(screen.getByText("Search all Heinzel conversations"));
  const input = screen.getByLabelText("Text");
  await user.type(input, "old");
  await user.click(screen.getByRole("button", { name: "Search" }));
  await user.clear(input);
  await user.type(input, "new");
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByText("New result")).toBeInTheDocument();
  await act(async () => {
    resolveOld({
      status: "done",
      session_id: "old-agent",
      items: [{ seq: 1, text: "Stale result", at: "2026-05-04T12:00:00Z" }],
      jump_cursors: ["old"],
      next_cursor: null,
      completeness: "unknown",
    });
    await old;
  });
  await waitFor(() => expect(screen.queryByText("Stale result")).not.toBeInTheDocument());
});

it("stops automatic work at a bounded batch and can be cancelled", async () => {
  const { calls } = mockApi({
    "/api/heinzel/search-agent-conversations": async () => ({
      status: "indexing",
      session_id: "agent-one",
      items: [],
      jump_cursors: [],
      next_cursor: "same-progress",
      completeness: "unknown",
    }),
  });
  const user = userEvent.setup();
  render(<OwnerConversationSearch onJump={vi.fn()} />);
  await user.click(screen.getByText("Search all Heinzel conversations"));
  await user.type(screen.getByLabelText("Text"), "needle");
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByText(/Search paused/)).toBeInTheDocument();
  expect(calls).toHaveLength(20);
  expect(screen.getByRole("button", { name: "Continue search" })).toBeInTheDocument();
  await user.clear(screen.getByLabelText("Text"));
  expect(screen.queryByText(/Search paused/)).not.toBeInTheDocument();
});

it("refuses an oversized page instead of retaining unbounded results", async () => {
  mockApi({
    "/api/heinzel/search-agent-conversations": () => ({
      status: "results",
      session_id: "agent-one",
      items: Array.from({ length: 51 }, (_, seq) => ({ seq, text: "Private", at: "2026-05-04T12:00:00Z" })),
      jump_cursors: Array.from({ length: 51 }, () => "cursor"),
      next_cursor: "next",
      completeness: "unknown",
    }),
  });
  const user = userEvent.setup();
  render(<OwnerConversationSearch onJump={vi.fn()} />);
  await user.click(screen.getByText("Search all Heinzel conversations"));
  await user.type(screen.getByLabelText("Text"), "needle");
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be completed");
  expect(screen.queryByText("Private")).not.toBeInTheDocument();
});
