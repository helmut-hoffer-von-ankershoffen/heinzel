import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mockApi } from "@/test/render";
import { ConversationSearch } from "./ConversationSearch";

const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";

describe("conversation search", () => {
  it("searches privately, pages results and opens the exact host-supplied message position", async () => {
    const { calls } = mockApi({
      "/api/heinzel/indexed-conversation-search": async (request: Request) => {
        const body = await request.clone().json();
        return {
          session_id: sessionId,
          items: [
            {
              seq: body.request.cursor ? 4 : 2,
              text: body.request.cursor ? "Second match" : "First match",
              at: "2026-05-04T12:00:00Z",
            },
          ],
          jump_cursors: [body.request.cursor ? "jump-two" : "jump-one"],
          next_cursor: body.request.cursor ? null : "next-page",
          mode: "trigram",
          completeness: "unknown",
        };
      },
    });
    const onJump = vi.fn();
    const user = userEvent.setup();
    render(<ConversationSearch sessionId={sessionId} onJump={onJump} />);
    await user.click(screen.getByText("Search conversation"));
    await user.type(screen.getByLabelText("Text"), "needle");
    await user.click(screen.getByRole("button", { name: /^Search$/ }));
    expect(await screen.findByText("First match")).toBeInTheDocument();
    expect(screen.getByText(/Results may be incomplete/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Search more" }));
    expect(await screen.findByText("Second match")).toBeInTheDocument();
    expect(screen.queryByText("First match")).not.toBeInTheDocument();
    const secondOpen = screen.getByRole("button", { name: "Open message" });
    if (!secondOpen || !calls[0]) throw new Error("Missing search result or request");
    await user.click(secondOpen);
    expect(onJump).toHaveBeenCalledWith("jump-two");
    expect(await calls[0]?.clone().json()).toMatchObject({
      request: { session_id: sessionId, query: "needle", cursor: null },
    });
    expect(new URL(calls[0].url).search).toBe("");
    expect(await calls[1]?.clone().json()).toMatchObject({ request: { cursor: "next-page" } });
  });

  it("rejects a mismatched session and does not display its private text", async () => {
    mockApi({
      "/api/heinzel/indexed-conversation-search": {
        session_id: "other-session",
        items: [{ seq: 1, text: "Other private content", at: "2026-05-04T12:00:00Z" }],
        jump_cursors: ["cursor"],
        next_cursor: null,
        mode: "trigram",
        completeness: "unknown",
      },
    });
    const user = userEvent.setup();
    render(<ConversationSearch sessionId={sessionId} onJump={vi.fn()} />);
    await user.click(screen.getByText("Search conversation"));
    await user.type(screen.getByLabelText("Text"), "needle");
    await user.click(screen.getByRole("button", { name: /^Search$/ }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Other private content")).not.toBeInTheDocument();
  });

  it("sends an explicit time bound and labels incremental time-only scans", async () => {
    const { calls } = mockApi({
      "/api/heinzel/indexed-conversation-search": {
        session_id: sessionId,
        items: [],
        jump_cursors: [],
        next_cursor: "continue",
        mode: "bounded-scan",
        completeness: "unknown",
      },
    });
    const user = userEvent.setup();
    render(<ConversationSearch sessionId={sessionId} onJump={vi.fn()} />);
    await user.click(screen.getByText("Search conversation"));
    await user.type(screen.getByLabelText("From"), "2026-05-04T12:00");
    await user.click(screen.getByRole("button", { name: /^Search$/ }));
    expect(await screen.findByText(/Short or time-only searches scan one page at a time/)).toBeInTheDocument();
    expect(screen.getByText("No matches in the scanned portion.")).toBeInTheDocument();
    expect(await calls[0]?.clone().json()).toMatchObject({ request: { query: "", since: expect.any(String) } });
    expect(screen.getByRole("button", { name: "Search more" })).toBeInTheDocument();
  });
});
