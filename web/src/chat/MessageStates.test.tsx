// Message states under the bubble (ADR 0157, canvas board 7): "Not sent · Try again", "Not answered · Try
// again" and "Queued" stand under the person's own message; the composer has no "Retry the same message" any more, and
// an uncertain turn never gets a plain retry.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { base } from "@/test/chat";
import { mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";

const helmi = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const at = "2026-11-14T09:00:00Z";

type Message = { id: string; seq: number; role: string; text: string; status?: string };

function mockHelmi(messages: Message[], extra: Record<string, unknown> = {}) {
  return mockApi({
    ...base,
    "/api/system/runner/chat/heinzel-actions": () => [],
    "/api/heinzel/sessions": {
      sessions: [{ session_id: helmi, name: "Helmi", state: "awake", last_seq: 1, updated_at: at }],
    },
    "/api/heinzel/session": () => ({ session_id: helmi, heinzel_id: helmi, name: "Helmi", messages }),
    ...extra,
  });
}

const paths = (calls: Request[], path: string) => calls.filter((request) => new URL(request.url).pathname === path);

async function send(text: string) {
  const user = userEvent.setup();
  const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
  await chat.findByRole("button", { name: /^Helmi, .+\. Switch Heinzel$/ });
  await user.type(chat.getByLabelText("Message Helmi"), text);
  await user.click(chat.getByRole("button", { name: "Send" }));
  return { chat, user };
}

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

describe("message states", () => {
  it("says Not sent under the bubble and tries the same message again from there", async () => {
    let tries = 0;
    const { calls } = mockHelmi([], {
      "/api/heinzel/chat": async (request: Request) => {
        tries += 1;
        if (tries === 1) return { status: 503, body: { detail: "acknowledgement lost" } };
        const body = await request.clone().json();
        return { session_id: helmi, request_id: body.request_id, disposition: "accepted", inbox_position: 1 };
      },
      "/api/heinzel/answer-chat": async (request: Request) => {
        const body = await request.clone().json();
        return {
          session_id: helmi,
          request_id: body.request_id,
          message: { id: "a1", seq: 2, role: "assistant", text: "Morning." },
        };
      },
    });
    renderAt("/doctor");
    const { chat, user } = await send("Good morning");
    const failure = await chat.findByRole("alert");
    expect(failure).toHaveTextContent("Not sent");
    expect(chat.getByText("Good morning")).toBeInTheDocument();
    expect(chat.queryByRole("button", { name: "Retry the same message" })).not.toBeInTheDocument();
    expect(chat.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.click(within(failure).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(paths(calls, "/api/heinzel/answer-chat")).toHaveLength(1));
    const sent = await Promise.all(paths(calls, "/api/heinzel/chat").map((request) => request.clone().json()));
    expect(sent).toHaveLength(2);
    expect(sent[1].request_id).toBe(sent[0].request_id);
    expect(sent[1].message).toBe("Good morning");
  });

  it("says Not answered when the message went in but the answer failed, and asks again without resending", async () => {
    let answers = 0;
    const { calls } = mockHelmi([], {
      "/api/heinzel/chat": async (request: Request) => {
        const body = await request.clone().json();
        return { session_id: helmi, request_id: body.request_id, disposition: "accepted", inbox_position: 1 };
      },
      "/api/heinzel/answer-chat": async (request: Request) => {
        answers += 1;
        if (answers === 1) return { status: 503, body: { detail: "busy" } };
        const body = await request.clone().json();
        return {
          session_id: helmi,
          request_id: body.request_id,
          message: { id: "a1", seq: 2, role: "assistant", text: "Here." },
        };
      },
    });
    renderAt("/doctor");
    const { chat, user } = await send("Are you there?");
    const failure = await chat.findByRole("alert");
    expect(failure).toHaveTextContent("Not answered");
    await user.click(within(failure).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(answers).toBe(2));
    expect(paths(calls, "/api/heinzel/chat")).toHaveLength(1);
    await waitFor(() => expect(chat.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("shows Queued under a message that waits for the one above", async () => {
    mockHelmi([
      { id: "m1", seq: 1, role: "user", text: "First", status: "pending" },
      { id: "m2", seq: 2, role: "user", text: "Second", status: "pending" },
    ]);
    renderAt("/doctor");
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    const queued = await chat.findByText("Queued · after the one above");
    expect(queued.closest("[data-message-state]")).toHaveAttribute("data-message-state", "queued");
    // The word stands under the bubble, not a second time inside it.
    expect(chat.queryByText("Queued")).not.toBeInTheDocument();
    // Only the second waits; the first is being answered and has nothing above it.
    expect(document.querySelectorAll("[data-message-state=queued]")).toHaveLength(1);
  });

  it("shows a failure found on reopening without announcing it", async () => {
    sessionStorage.setItem(
      `hz.durable.composer.${helmi}`,
      JSON.stringify({
        version: 1,
        session_id: helmi,
        text: "",
        pending: { request_id: "5f0c6c1e-6a4f-4c8e-9a52-3b1d2f7e8a90", message: "Left behind" },
        agentPending: null,
        checkInRequest: null,
      }),
    );
    mockHelmi([]);
    renderAt("/doctor");
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    const line = await chat.findByText("Not sent");
    expect(line.closest("[data-message-state]")).not.toHaveAttribute("role");
    await waitFor(() => expect(chat.getByRole("button", { name: "Try again" })).toBeEnabled());
  });

  it("never offers Try again for an uncertain turn", async () => {
    const turns: unknown[] = [];
    mockHelmi([], {
      "/api/heinzel/chat": async (request: Request) => {
        const body = await request.clone().json();
        return { session_id: helmi, request_id: body.request_id, disposition: "queued", inbox_position: 2 };
      },
      "/api/heinzel/answer-chat": async (request: Request) => {
        turns.push(await request.clone().json());
        return { status: 400, body: { code: "subscription_usage_limit", error: "redacted status" } };
      },
      "/api/heinzel/chat-turn-state": (request: Request) => ({
        session_id: helmi,
        request_id: new URL(request.url).searchParams.get("request_id"),
        status: "unresolved",
      }),
    });
    renderAt("/doctor");
    const { chat } = await send("Check my notes");
    expect(await chat.findByText(/reported its subscription usage limit/)).toBeInTheDocument();
    expect(chat.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(chat.queryByRole("button", { name: "Retry the same message" })).not.toBeInTheDocument();
    expect(turns).toHaveLength(1);
  });

  describe.each([
    ["unresolved", 0],
    ["abandoned", 0],
    ["completed", 1],
    ["pending", 1],
  ])("after a reload with turn state %s", (status, tryAgain) => {
    it(`shows ${tryAgain} Try again button(s)`, async () => {
      sessionStorage.setItem(
        `hz.durable.composer.${helmi}`,
        JSON.stringify({
          version: 1,
          session_id: helmi,
          text: "",
          pending: { request_id: "req-1", message: "Check my notes", accepted: true },
          agentPending: null,
          checkInRequest: null,
        }),
      );
      mockHelmi([], {
        "/api/heinzel/chat-turn-state": () => ({ session_id: helmi, request_id: "req-1", status }),
      });
      renderAt("/doctor");
      const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
      await chat.findByText("Check my notes");
      await waitFor(() => expect(chat.queryAllByRole("button", { name: "Try again" })).toHaveLength(tryAgain));
    });
  });
});
