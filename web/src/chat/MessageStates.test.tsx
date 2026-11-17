// Message states under the bubble (ADR 0157, canvas board 7): "Not sent · Try again", "Not answered · Try
// again" and "Queued" stand under the person's own message; the composer has no "Retry the same message" any more, and
// an uncertain turn never gets a plain retry.
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { Providers, queryClient, routes } from "@/App";
import { base } from "@/test/chat";
import { mockApi, renderAt } from "@/test/render";

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
  it.each(["authority", "session", "unmount"])(
    "never answers after %s changes during the queue state recheck",
    async (change) => {
      let reads = 0;
      let release: ((value: unknown) => void) | undefined;
      let replacement: ((value: unknown) => void) | undefined;
      let oldRequest: Request | undefined;
      const { calls } = mockHelmi([], {
        "/api/heinzel/session": (request: Request) => ({
          session_id: new URL(request.url).searchParams.get("session_id"),
          heinzel_id: new URL(request.url).searchParams.get("session_id"),
          name: "Helmi",
          messages: [],
          queue_head:
            new URL(request.url).searchParams.get("session_id") === helmi
              ? { id: "earlier", seq: 1, role: "user", origin: "web-session", text: "Earlier", status: "pending" }
              : null,
        }),
        "/api/heinzel/chat-turn-state": (request: Request) => {
          if (request.headers.get("Authorization") === "Bearer replacement-token")
            return new Promise((resolve) => {
              replacement = resolve;
            });
          reads++;
          if (reads === 1) return { session_id: helmi, request_id: "earlier", status: "pending" };
          oldRequest = request;
          return new Promise((resolve) => {
            release = resolve;
          });
        },
        "/api/heinzel/answer-chat": {
          session_id: helmi,
          request_id: "earlier",
          message: { role: "assistant", text: "Must not start" },
        },
      });
      sessionStorage.setItem("hz.token", "test-token");
      const router = createMemoryRouter(routes, { initialEntries: ["/doctor"] });
      const view = render(
        <Providers client={queryClient()}>
          <RouterProvider router={router} />
        </Providers>,
      );
      const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
      await userEvent.click(await chat.findByRole("button", { name: "Answer this earlier message" }));
      await waitFor(() => expect(release).toBeDefined());
      if (change === "authority") {
        act(() => {
          sessionStorage.setItem("hz.token", "replacement-token");
          window.dispatchEvent(new Event("storage"));
        });
        await waitFor(() => expect(replacement).toBeDefined());
        expect(screen.queryByRole("button", { name: "Answer this earlier message" })).not.toBeInTheDocument();
        await act(async () => {
          replacement?.({ session_id: helmi, request_id: "earlier", status: "unresolved" });
        });
        expect(await screen.findByRole("checkbox", { name: /will not retry/ })).not.toBeChecked();
      } else if (change === "session") {
        await act(async () => router.navigate("/heinzel?chat=hz-01ARZ3NDEKTSV4RRFFQ69G5FAW"));
      } else view.unmount();
      await act(async () => {
        release?.({ session_id: helmi, request_id: "earlier", status: "pending" });
      });
      expect(oldRequest?.signal.aborted).toBe(true);
      expect(paths(calls, "/api/heinzel/answer-chat")).toHaveLength(0);
      if (change === "authority") {
        expect(screen.queryByRole("button", { name: "Answer this earlier message" })).not.toBeInTheDocument();
        expect(screen.getByRole("checkbox", { name: /will not retry/ })).not.toBeChecked();
        expect(reads).toBe(2);
      }
    },
  );

  it("explicitly cancels an expired unstarted human head without answering or replaying", async () => {
    let status = "pending";
    const { calls } = mockHelmi([], {
      "/api/heinzel/session": () => ({
        session_id: helmi,
        heinzel_id: helmi,
        name: "Helmi",
        messages: [],
        queue_head:
          status === "abandoned"
            ? null
            : {
                id: "expired",
                seq: 1,
                role: "user",
                origin: "web-session",
                text: "No longer retained",
                status: "expired",
              },
      }),
      "/api/heinzel/chat-turn-state": () => ({ session_id: helmi, request_id: "expired", status }),
      "/api/heinzel/abandon-chat-turn": async (request: Request) => {
        expect(await request.json()).toEqual({
          session_id: helmi,
          request_id: "expired",
          acknowledge_uncertain: true,
          expected_status: "pending",
        });
        status = "abandoned";
        return { session_id: helmi, request_id: "expired", status };
      },
    });
    renderAt("/doctor");
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    const recovery = within(await chat.findByRole("region", { name: "Resolve an uncertain turn" }));
    expect(chat.getByLabelText("Message Helmi")).toBeDisabled();
    expect(chat.queryByRole("button", { name: "Answer this earlier message" })).not.toBeInTheDocument();
    const cancel = await recovery.findByRole("button", { name: "Set aside this unstarted turn" });
    expect(cancel).toBeDisabled();
    await userEvent.click(recovery.getByRole("checkbox", { name: /expired, unstarted input/ }));
    await userEvent.click(cancel);
    await userEvent.click(await recovery.findByRole("button", { name: "Continue conversation" }));
    await waitFor(() => expect(chat.getByLabelText("Message Helmi")).toBeEnabled());
    expect(paths(calls, "/api/heinzel/answer-chat")).toHaveLength(0);
    expect(paths(calls, "/api/heinzel/abandon-chat-turn")).toHaveLength(1);
  });

  it("requires fresh uncertain-outcome consent if an expired input starts", async () => {
    let status = "pending";
    const { calls } = mockHelmi([], {
      "/api/heinzel/session": () => ({
        session_id: helmi,
        heinzel_id: helmi,
        name: "Helmi",
        messages: [],
        queue_head: {
          id: "expired",
          seq: 1,
          role: "user",
          origin: "web-session",
          text: "No longer retained",
          status: "expired",
        },
      }),
      "/api/heinzel/chat-turn-state": () => ({ session_id: helmi, request_id: "expired", status }),
    });
    renderAt("/doctor");
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    const recovery = within(await chat.findByRole("region", { name: "Resolve an uncertain turn" }));
    await userEvent.click(await recovery.findByRole("checkbox", { name: /expired, unstarted input/ }));
    status = "unresolved";
    await userEvent.click(recovery.getByRole("button", { name: "Check saved turn" }));
    expect(await recovery.findByRole("checkbox", { name: /will not retry/ })).not.toBeChecked();
    expect(recovery.getByRole("button", { name: "Set aside this turn" })).toBeDisabled();
    expect(paths(calls, "/api/heinzel/abandon-chat-turn")).toHaveLength(0);
  });

  it("exposes recovery for an earlier uncertain turn without replaying it", async () => {
    let status = "unresolved";
    const { calls } = mockHelmi(
      [
        { id: "earlier", seq: 1, role: "user", text: "First", status: "pending" },
        { id: "later", seq: 2, role: "user", text: "Test", status: "pending" },
      ],
      {
        "/api/heinzel/session": () => ({
          session_id: helmi,
          heinzel_id: helmi,
          name: "Helmi",
          messages: [{ id: "later", seq: 2, role: "user", text: "Test", status: "pending" }],
          queue_head: { id: "earlier", seq: 1, role: "user", origin: "web-session", text: "First", status: "pending" },
        }),
        "/api/heinzel/chat-turn-state": (request: Request) => ({
          session_id: helmi,
          request_id: new URL(request.url).searchParams.get("request_id"),
          status: new URL(request.url).searchParams.get("request_id") === "earlier" ? status : "pending",
        }),
        "/api/heinzel/abandon-chat-turn": async (request: Request) => {
          const body = await request.clone().json();
          expect(body.request_id).toBe("earlier");
          expect(body.acknowledge_uncertain).toBe(true);
          expect(body.expected_status).toBe("unresolved");
          status = "abandoned";
          return { session_id: helmi, request_id: "earlier", status };
        },
      },
    );
    renderAt("/doctor");
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    const recovery = within(await chat.findByRole("region", { name: "Resolve an uncertain turn" }));
    const user = userEvent.setup();
    await user.click(recovery.getByRole("checkbox"));
    await user.click(recovery.getByRole("button", { name: "Set aside this turn" }));
    expect(await recovery.findByText(/This turn was set aside/)).toBeInTheDocument();
    expect(paths(calls, "/api/heinzel/answer-chat")).toHaveLength(0);
  });

  it("answers only the earliest unstarted message and then exposes the next", async () => {
    let firstDone = false;
    const head = (id: string, seq: number) => ({
      id,
      seq,
      role: "user",
      origin: "web-session",
      text: id,
      status: "pending",
    });
    const { calls } = mockHelmi([], {
      "/api/heinzel/session": () => ({
        session_id: helmi,
        heinzel_id: helmi,
        name: "Helmi",
        messages: [],
        queue_head: firstDone ? head("second", 2) : head("first", 1),
      }),
      "/api/heinzel/chat-turn-state": (request: Request) => ({
        session_id: helmi,
        request_id: new URL(request.url).searchParams.get("request_id"),
        status: firstDone && new URL(request.url).searchParams.get("request_id") === "first" ? "completed" : "pending",
      }),
      "/api/heinzel/answer-chat": async (request: Request) => {
        const body = await request.clone().json();
        expect(body.request_id).toBe("first");
        firstDone = true;
        return {
          session_id: helmi,
          request_id: "first",
          message: { id: "reply", seq: 3, role: "assistant", text: "Done" },
        };
      },
    });
    renderAt("/doctor");
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    const user = userEvent.setup();
    await user.click(await chat.findByRole("button", { name: "Answer this earlier message" }));
    await waitFor(() => expect(paths(calls, "/api/heinzel/answer-chat")).toHaveLength(1));
    await waitFor(() => expect(chat.getByText("second")).toBeInTheDocument());
  });

  it("keeps recovery beside the composer and refreshes a completed stale head", async () => {
    let completed = false;
    const messages = Array.from({ length: 50 }, (_, i) => ({
      id: `m${i}`,
      seq: i + 2,
      role: "assistant",
      text: `Reply ${i}`,
    }));
    mockHelmi(messages, {
      "/api/heinzel/session": () => ({
        session_id: helmi,
        heinzel_id: helmi,
        name: "Helmi",
        messages,
        queue_head: completed
          ? null
          : { id: "old", seq: 1, role: "user", origin: "web-session", text: "Earlier", status: "pending" },
      }),
      "/api/heinzel/chat-turn-state": () => ({
        session_id: helmi,
        request_id: "old",
        status: completed ? "completed" : "pending",
      }),
    });
    renderAt("/doctor");
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    const blocker = await chat.findByRole("region", { name: "Earlier message blocking the queue" });
    const footer = blocker.parentElement?.parentElement;
    expect(footer).toContainElement(chat.getByLabelText("Message Helmi"));
    expect(footer).not.toContainElement(chat.getByText("Reply 49"));
    expect(chat.getByLabelText("Message Helmi")).toBeDisabled();
    completed = true;
    await userEvent.click(within(blocker).getByRole("button", { name: "Check earlier turn" }));
    await waitFor(() =>
      expect(chat.queryByRole("region", { name: "Earlier message blocking the queue" })).not.toBeInTheDocument(),
    );
    expect(chat.getByLabelText("Message Helmi")).toBeEnabled();
  });

  it("requires a new acknowledgement when a daemon turn starts", async () => {
    let status = "pending";
    const { calls } = mockHelmi([], {
      "/api/heinzel/session": () => ({
        session_id: helmi,
        heinzel_id: helmi,
        name: "Helmi",
        messages: [],
        queue_head: { id: "wake", seq: 1, role: "user", origin: "daemon", text: "Wake", status: "pending" },
      }),
      "/api/heinzel/chat-turn-state": () => ({ session_id: helmi, request_id: "wake", status }),
    });
    renderAt("/doctor");
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    const recovery = within(await chat.findByRole("region", { name: "Resolve an uncertain turn" }));
    await userEvent.click(await recovery.findByRole("checkbox", { name: /unstarted daemon turn/ }));
    status = "unresolved";
    await userEvent.click(recovery.getByRole("button", { name: "Check saved turn" }));
    const uncertain = await recovery.findByRole("checkbox", { name: /will not retry/ });
    expect(uncertain).not.toBeChecked();
    expect(recovery.getByRole("button", { name: "Set aside this turn" })).toBeDisabled();
    expect(paths(calls, "/api/heinzel/abandon-chat-turn")).toHaveLength(0);
  });

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
