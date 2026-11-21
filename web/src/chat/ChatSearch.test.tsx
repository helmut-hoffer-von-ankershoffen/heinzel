import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/app/providers";
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
    expect(Object.keys(body.request).sort()).toEqual(["cursor", "query", "session_id", "since", "until"]);
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

  const allPage = (over: Record<string, unknown>) => ({
    status: "results",
    session_id: other,
    items: [],
    jump_cursors: [],
    next_cursor: null,
    completeness: "unknown",
    ...over,
  });
  const item = (text: string) => ({ seq: 1, text, at: "2026-05-04T12:00:00Z" });
  const searchAllFor = async (user: ReturnType<typeof userEvent.setup>, text = "needle") => {
    await user.click(screen.getByRole("radio", { name: "All Heinzels" }));
    await user.type(screen.getByLabelText("Search messages"), `${text}{Enter}`);
  };

  it("refuses a page of another chat and never shows its text", async () => {
    const { user } = setup({
      "/api/heinzel/indexed-conversation-search": { ...chatPage(null), session_id: other },
    });
    await user.type(screen.getByLabelText("Search messages"), "needle{Enter}");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("First match")).not.toBeInTheDocument();
  });

  it("refuses an unknown index mode and a missing or mismatched cursor", async () => {
    const { user } = setup({
      "/api/heinzel/indexed-conversation-search": { ...chatPage(null), mode: "other" },
    });
    await user.type(screen.getByLabelText("Search messages"), "needle{Enter}");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("refuses an oversized page and a page whose cursors do not match", async () => {
    const { user } = setup({
      "/api/heinzel/search-agent-conversations": allPage({
        items: Array.from({ length: 51 }, (_, seq) => ({ ...item("Private"), seq })),
        jump_cursors: Array.from({ length: 51 }, () => "c"),
      }),
    });
    await searchAllFor(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be completed");
    expect(screen.queryByText("Private")).not.toBeInTheDocument();
  });

  it("refuses an item without a jump cursor", async () => {
    const { user } = setup({
      "/api/heinzel/search-agent-conversations": allPage({ items: [item("No cursor")], jump_cursors: [] }),
    });
    await searchAllFor(user);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("No cursor")).not.toBeInTheDocument();
  });

  it("sends a time bound with no text and accepts the bounded scan", async () => {
    const { user, calls } = setup({
      "/api/heinzel/indexed-conversation-search": {
        ...chatPage(null),
        items: [],
        jump_cursors: [],
        mode: "bounded-scan",
      },
    });
    await user.click(screen.getByText("Date range"));
    await user.type(screen.getByLabelText("From"), "2026-05-04T12:00");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText(/No matches|Nothing yet/)).toBeInTheDocument();
    const body = await calls
      .find((call) => call.url.includes("indexed"))
      ?.clone()
      .json();
    expect(body.request).toMatchObject({ query: "", since: expect.any(String), until: null });
  });

  it("skips chats without matches and goes on until it finds some", async () => {
    const { user, calls, onJump } = setup({
      "/api/heinzel/search-agent-conversations": async (request: Request) => {
        const { request: body } = await request.clone().json();
        if (!body.cursor) return allPage({ status: "indexing", next_cursor: "one" });
        return allPage({ items: [item("Found later")], jump_cursors: ["jump-later"] });
      },
    });
    await searchAllFor(user);
    await user.click(await screen.findByRole("button", { name: /Found later/ }));
    expect(onJump).toHaveBeenCalledWith(other, "jump-later");
    expect(calls.filter((call) => call.url.includes("search-agent"))).toHaveLength(2);
  });

  it("stops at a bounded batch, says nothing was found yet and can go on", async () => {
    const { user, calls } = setup({
      "/api/heinzel/search-agent-conversations": allPage({ status: "indexing", next_cursor: "same" }),
    });
    await searchAllFor(user);
    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument();
    expect(screen.queryByText(/No matches/)).not.toBeInTheDocument();
    expect(calls.filter((call) => call.url.includes("search-agent"))).toHaveLength(20);
    expect(screen.getByRole("button", { name: "Show more" })).toBeInTheDocument();
  });

  it("appends the next results and names an unknown Heinzel neutrally", async () => {
    const { user } = setup({
      "/api/heinzel/search-agent-conversations": async (request: Request) => {
        const { request: body } = await request.clone().json();
        return body.cursor
          ? allPage({ session_id: "hz-unknown", items: [item("Second")], jump_cursors: ["b"] })
          : allPage({ items: [item("First")], jump_cursors: ["a"], next_cursor: "more" });
      },
    });
    await searchAllFor(user);
    await screen.findByRole("button", { name: /First/ });
    await user.click(screen.getByRole("button", { name: "Show more" }));
    const second = await screen.findByRole("button", { name: /Second/ });
    expect(second).toHaveTextContent("Another Heinzel");
    expect(screen.getByRole("button", { name: /First/ })).toBeInTheDocument();
  });

  it("says when a chat could not be searched", async () => {
    const { user } = setup({
      "/api/heinzel/search-agent-conversations": allPage({
        status: "blocked",
        items: [item("Partial")],
        jump_cursors: ["p"],
        next_cursor: "after",
      }),
    });
    await searchAllFor(user);
    await screen.findByRole("button", { name: /Partial/ });
    expect(screen.getByText(/could not be searched/)).toBeInTheDocument();
  });

  it("discards a late response of an earlier query", async () => {
    let resolveOld!: (value: unknown) => void;
    const old = new Promise((resolve) => {
      resolveOld = resolve;
    });
    const { user } = setup({
      "/api/heinzel/search-agent-conversations": async (request: Request) => {
        const { request: body } = await request.clone().json();
        return body.query === "old"
          ? old
          : allPage({ session_id: here, items: [item("New result")], jump_cursors: ["n"] });
      },
    });
    await user.click(screen.getByRole("radio", { name: "All Heinzels" }));
    const field = screen.getByLabelText("Search messages");
    await user.type(field, "old{Enter}");
    await user.clear(field);
    await user.type(field, "new{Enter}");
    expect(await screen.findByText("New result")).toBeInTheDocument();
    await act(async () => {
      resolveOld(allPage({ items: [item("Stale result")], jump_cursors: ["s"] }));
      await old;
    });
    await waitFor(() => expect(screen.queryByText("Stale result")).not.toBeInTheDocument());
  });
});
