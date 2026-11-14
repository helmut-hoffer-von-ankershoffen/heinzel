import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { mockApi } from "@/test/render";
import { ChatSearch } from "./ChatSearch";

const here = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const other = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
const sessions = {
  sessions: [
    { session_id: here, name: "Helmi", state: "awake", last_seq: 1, updated_at: "2026-05-04T12:00:00Z" },
    { session_id: other, name: "Fritz", state: "awake", last_seq: 1, updated_at: "2026-05-04T12:00:00Z" },
  ],
};
const chatPage = (cursor: string | null) => ({
  session_id: here,
  items: [{ seq: cursor ? 4 : 2, text: cursor ? "Second match" : "First match", at: "2026-05-04T12:00:00Z" }],
  jump_cursors: [cursor ? "jump-two" : "jump-one"],
  next_cursor: cursor ? null : "next-page",
  mode: "trigram",
  completeness: "unknown",
});

function setup(answers: Record<string, unknown> = {}) {
  const onJump = vi.fn();
  const onClose = vi.fn();
  const api = mockApi({ "/api/heinzel/sessions": sessions, ...answers } as never);
  const user = userEvent.setup();
  render(
    <Providers client={queryClient()}>
      <ChatSearch sessionId={here} onJump={onJump} onClose={onClose} />
    </Providers>,
  );
  return { ...api, user, onJump, onClose };
}

describe("chat search", () => {
  it("is a labelled search with the field focused and 44 px targets", () => {
    setup();
    expect(screen.getByRole("search", { name: "Search Heinzel chats" })).toBeInTheDocument();
    expect(screen.getByLabelText("Search messages")).toHaveFocus();
    expect(screen.getByRole("button", { name: "Search" })).toHaveClass("min-h-11");
    expect(screen.getByRole("radio", { name: "This chat" })).toBeChecked();
  });

  it("searches this chat, announces the count politely, pages and jumps to the message", async () => {
    const { user, onJump, calls } = setup({
      "/api/heinzel/indexed-conversation-search": async (request: Request) =>
        chatPage((await request.clone().json()).request.cursor),
    });
    await user.type(screen.getByLabelText("Search messages"), "needle{Enter}");
    expect(await screen.findByRole("button", { name: /First match/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("1 result");
    await user.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByRole("button", { name: /Second match/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("2 results");
    await user.click(screen.getByRole("button", { name: /Second match/ }));
    expect(onJump).toHaveBeenCalledWith(here, "jump-two");
    const body = await calls
      .find((call) => call.url.includes("indexed"))
      ?.clone()
      .json();
    expect(body.request).toMatchObject({ session_id: here, query: "needle", cursor: null });
  });

  it("switches scope to all Heinzels, names the Heinzel and jumps into its chat", async () => {
    const { user, onJump, calls } = setup({
      "/api/heinzel/search-agent-conversations": {
        status: "results",
        session_id: other,
        items: [{ seq: 9, text: "Other private match", at: "2026-05-04T12:00:00Z" }],
        jump_cursors: ["owner-match"],
        next_cursor: null,
        completeness: "unknown",
      },
    });
    await user.click(screen.getByRole("radio", { name: "All Heinzels" }));
    await user.type(screen.getByLabelText("Search messages"), "match{Enter}");
    const result = await screen.findByRole("button", { name: /Other private match/ });
    expect(result).toHaveTextContent("Fritz");
    await user.click(result);
    expect(onJump).toHaveBeenCalledWith(other, "owner-match");
    expect(calls.some((call) => call.url.includes("indexed-conversation-search"))).toBe(false);
  });

  it("clears results when the scope changes", async () => {
    const { user } = setup({ "/api/heinzel/indexed-conversation-search": chatPage(null) });
    await user.type(screen.getByLabelText("Search messages"), "needle{Enter}");
    await screen.findByRole("button", { name: /First match/ });
    await user.click(screen.getByRole("radio", { name: "All Heinzels" }));
    expect(screen.queryByRole("button", { name: /First match/ })).not.toBeInTheDocument();
  });

  it("says so when nothing matches", async () => {
    const { user } = setup({
      "/api/heinzel/indexed-conversation-search": {
        session_id: here,
        items: [],
        jump_cursors: [],
        next_cursor: null,
        mode: "trigram",
        completeness: "unknown",
      },
    });
    await user.type(screen.getByLabelText("Search messages"), "zzz{Enter}");
    expect(await screen.findByText(/No matches/)).toBeInTheDocument();
  });

  it("shows an alert when the search fails", async () => {
    const { user } = setup({
      "/api/heinzel/indexed-conversation-search": { status: 500, body: { detail: "broken" } },
    });
    await user.type(screen.getByLabelText("Search messages"), "needle{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Search could not be completed");
  });

  it("does not search an empty field", async () => {
    const { user, calls } = setup();
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
    await user.type(screen.getByLabelText("Search messages"), "{Enter}");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("closes on Escape and on the Close button", async () => {
    const { user, onClose } = setup();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Close search" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
