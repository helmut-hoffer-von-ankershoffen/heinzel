import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseEvents } from "@/api/stream";
import { answered, base, empty, frame, mockWithHeldStream, mockWithStream, msg, proposal } from "@/test/chat";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

afterEach(() => sessionStorage.clear());

describe("ChatPanel", () => {
  it("clears the prior agent links when closing and creating another personal conversation", async () => {
    const previous = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    const next = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
    const oldChat = { ...empty, personal_session_id: previous };
    const { calls } = mockApi({
      ...base,
      "/api/heinzel/session": (request: Request) => {
        const id = new URL(request.url).searchParams.get("session_id");
        return {
          session_id: id,
          heinzel_id: id,
          name: id === next ? "New personal helper" : "Previous helper",
          messages: [],
        };
      },
      "/api/heinzel/new-chat": { session_id: next, heinzel_id: next, name: "New personal helper", messages: [] },
      "/api/heinzel/sessions": { sessions: [] },
      "/api/system/runner/chat/show": oldChat,
    });
    const user = userEvent.setup();
    renderAt(
      `/doctor?chat=${previous}&agent-chat=${oldChat.id}&approval-chat=${oldChat.id}&approval-request=old&agent-open-request=old`,
    );
    await user.click(await screen.findByRole("button", { name: "Back to chat" }));
    await user.click(await screen.findByRole("button", { name: /switch chats/ }));
    await user.click(await screen.findByRole("button", { name: /^Create a personal Heinzel$/ }));
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByRole("heading", { name: "New personal helper" })).toBeInTheDocument();
    await user.type(await panel.findByLabelText("Message your Heinzel"), "Keep this in the new conversation");
    expect(panel.getByRole("button", { name: "Queue message" })).toBeEnabled();
    const created = calls.findIndex((request) => new URL(request.url).pathname.endsWith("/heinzel/new-chat"));
    expect(
      calls.slice(created + 1).some((request) => new URL(request.url).pathname.endsWith("/runner/chat/show")),
    ).toBe(false);
  });

  it("takes a workshop sentence into a new agent chat without sending or losing it", async () => {
    const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    const { calls } = mockApi({
      ...base,
      "/api/heinzel/new-chat": { session_id: id, heinzel_id: id, name: "New personal helper", messages: [] },
      "/api/heinzel/session": { session_id: id, heinzel_id: id, name: "New personal helper", messages: [] },
      "/api/heinzel/sessions": { sessions: [] },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.type(screen.getByLabelText("Your message"), "Keep my notes current");
    await user.click(screen.getByRole("button", { name: "Create a helper and take this message" }));
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByLabelText("Message your Heinzel")).toHaveValue("Keep my notes current");
    expect(panel.getByRole("button", { name: "Queue message" })).toBeEnabled();
    expect(calls.some((request) => new URL(request.url).pathname === "/api/heinzel/chat")).toBe(false);
    expect(JSON.parse(sessionStorage.getItem(`hz.durable.composer.${id}`) ?? "null").text).toBe(
      "Keep my notes current",
    );
  });

  it("takes a request from workshop into an agent and shows its saved proposal", async () => {
    const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    let messages: object[] = [];
    let proposed: object | null = null;
    const session = () => ({ session_id: id, heinzel_id: id, name: "New personal helper", messages });
    const { calls } = mockApi({
      ...base,
      "/api/heinzel/new-chat": session,
      "/api/heinzel/session": session,
      "/api/heinzel/sessions": { sessions: [] },
      "/api/heinzel/latest-scheduled-agent-proposal": () => proposed,
      "/api/heinzel/chat": async (request: Request) => {
        const body = await request.json();
        messages = [{ id: body.request_id, seq: 1, role: "user", text: body.message, status: "pending" }];
        return { session_id: id, request_id: body.request_id, disposition: "queued", inbox_position: 1 };
      },
      "/api/heinzel/answer-chat": async (request: Request) => {
        const body = await request.json();
        const message = {
          id: "reply",
          seq: 2,
          role: "assistant",
          text: "I prepared terms for review.",
          status: "completed",
        };
        messages = [...messages, message];
        proposed = {
          session_id: id,
          request_id: "proposal",
          revision: 1,
          contract: "Keep the chosen artifact current.",
          max_usd: null,
          timeout_seconds: 30,
          permitted_tools: [],
          status: "proposed",
          preview_sha256: "a".repeat(64),
        };
        return { session_id: id, request_id: body.request_id, message };
      },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.type(screen.getByLabelText("Your message"), "Keep the chosen artifact current");
    await user.click(screen.getByRole("button", { name: "Create a helper and take this message" }));
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByLabelText("Message your Heinzel")).toHaveValue("Keep the chosen artifact current");
    await user.click(panel.getByRole("button", { name: "Queue message" }));
    expect(await panel.findByText("I prepared terms for review.")).toBeInTheDocument();
    const review = within(await panel.findByRole("region", { name: "Proposed agent terms" }));
    expect(review.getByText("Revision 1 · proposed terms")).toBeInTheDocument();
    expect(review.getByText(/does not activate the agent/)).toBeInTheDocument();
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/confirm"))).toBe(false);
  });

  it("locks workshop text during creation so it cannot erase a later edit", async () => {
    const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    let finish: ((value: object) => void) | undefined;
    mockApi({
      ...base,
      "/api/heinzel/new-chat": () =>
        new Promise<object>((resolve) => {
          finish = resolve;
        }),
      "/api/heinzel/session": { session_id: id, heinzel_id: id, name: "New personal helper", messages: [] },
      "/api/heinzel/sessions": { sessions: [] },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const box = screen.getByLabelText("Your message");
    await user.type(box, "First request");
    await user.click(screen.getByRole("button", { name: "Create a helper and take this message" }));
    expect(await screen.findByText("Creating your helper…")).toBeInTheDocument();
    expect(box).toBeDisabled();
    await user.type(box, " later edit");
    expect(box).toHaveValue("First request");
    finish?.({ session_id: id, heinzel_id: id, name: "New personal helper", messages: [] });
    expect(await screen.findByLabelText("Message your Heinzel")).toHaveValue("First request");
  });

  it("recovers a workshop sentence and creation ID after a reload before acknowledgement", async () => {
    const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    const requests: string[] = [];
    mockApi({
      ...base,
      "/api/heinzel/new-chat": async (request: Request) => {
        requests.push((await request.json()).request_id);
        return new Promise(() => {});
      },
    });
    const user = userEvent.setup();
    const first = renderAt("/doctor");
    await user.type(screen.getByLabelText("Your message"), "Preserve across reload");
    await user.click(screen.getByRole("button", { name: "Create a helper and take this message" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    first.unmount();
    mockApi({
      ...base,
      "/api/heinzel/new-chat": async (request: Request) => {
        requests.push((await request.json()).request_id);
        return { session_id: id, heinzel_id: id, name: "New personal helper", messages: [] };
      },
      "/api/heinzel/session": { session_id: id, heinzel_id: id, name: "New personal helper", messages: [] },
      "/api/heinzel/sessions": { sessions: [] },
    });
    renderAt("/doctor");
    await waitFor(() => expect(screen.getByLabelText("Your message")).toHaveValue("Preserve across reload"));
    expect(screen.getByLabelText("Your message")).toHaveAttribute("readonly");
    expect(screen.getByText(/earlier creation is unresolved/)).toBeInTheDocument();
    expect(requests).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Create a helper and take this message" }));
    expect(await screen.findByLabelText("Message your Heinzel")).toHaveValue("Preserve across reload");
    expect(requests).toEqual([requests[0], requests[0]]);
    expect(sessionStorage.getItem("hz.personal-heinzel.handoff")).toBeNull();
  });

  it("keeps the sentence and creation identity when local draft storage fails", async () => {
    const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    const requests: string[] = [];
    const original = Storage.prototype.setItem;
    let fail = true;
    const setter = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (fail && key === `hz.durable.composer.${id}`) throw new Error("storage unavailable");
      return original.call(this, key, value);
    });
    mockApi({
      ...base,
      "/api/heinzel/new-chat": async (request: Request) => {
        requests.push((await request.json()).request_id);
        return { session_id: id, heinzel_id: id, name: "New personal helper", messages: [] };
      },
      "/api/heinzel/session": { session_id: id, heinzel_id: id, name: "New personal helper", messages: [] },
      "/api/heinzel/sessions": { sessions: [] },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const box = screen.getByLabelText("Your message");
    await user.type(box, "Preserve this request");
    await user.click(screen.getByRole("button", { name: "Create a helper and take this message" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(box).toHaveValue("Preserve this request");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Create a helper and take this message" }));
    expect(await screen.findByLabelText("Message your Heinzel")).toHaveValue("Preserve this request");
    expect(requests).toHaveLength(2);
    expect(requests[1]).toBe(requests[0]);
    setter.mockRestore();
  });

  it("does not overwrite a recovered agent draft when taking a workshop sentence", async () => {
    const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    const key = `hz.durable.composer.${id}`;
    const saved = JSON.stringify({
      version: 1,
      session_id: id,
      text: "Earlier unsent work",
      pending: null,
      agentPending: null,
      checkInRequest: null,
    });
    sessionStorage.setItem(key, saved);
    mockApi({
      ...base,
      "/api/heinzel/new-chat": { session_id: id, heinzel_id: id, name: "New personal helper", messages: [] },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const box = screen.getByLabelText("Your message");
    await user.type(box, "A different request");
    await user.click(screen.getByRole("button", { name: "Create a helper and take this message" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(box).toHaveValue("A different request");
    expect(sessionStorage.getItem(key)).toBe(saved);
    localStorage.removeItem("hz.personal-heinzel.creation");
  });

  it("names the per-message and per-chat caps before anything is sent", async () => {
    mockApi(base);
    renderAt("/doctor");
    expect(await screen.findByText(/at most \$0\.25 per message/)).toBeInTheDocument();
  });

  it("opens a session about the current screen, streams the answer, and shows its cost", async () => {
    const { calls } = mockWithStream({ ...base, "/api/system/runner/chat/open": empty }, [
      frame("tool", { name: "system_doctor" }),
      frame("text", { text: "I proposed" }),
      frame("result", { data: answered }),
    ]);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.type(screen.getByLabelText("Your message"), "Make me the lead{Enter}");
    expect(await screen.findByText("I proposed setting your role to lead.")).toBeInTheDocument();
    await user.hover(screen.getByRole("button", { name: "About this answer" }));
    const about = screen.getByRole("tooltip");
    expect(about).toHaveTextContent("$0.0123");
    const open = calls.find((request) => request.url.includes("/api/system/runner/chat/open"));
    expect(new URL(open?.url ?? "").searchParams.get("context")).toBe("route:/doctor");
    const send = calls.find((request) => request.url.includes("/api/stream/system/runner/chat/send"));
    expect(send?.method).toBe("POST");
    expect(send?.headers.get("Authorization")).toBe("Bearer test-token");
    expect(new URL(send?.url ?? "").searchParams.get("session")).toBe(empty.id);
    expect(new URL(send?.url ?? "").searchParams.get("message")).toBe("Make me the lead");
  });

  it("shows a proposed action as a card, and runs it only on Confirm", async () => {
    sessionStorage.setItem("hz.chat", answered.id);
    const done = {
      ...answered,
      actions: [{ ...proposal, status: "done" as const, result: "{}" }],
      messages: [...(answered.messages ?? []), msg({ role: "action", text: `Ran \`${proposal.command}\`: {}` })],
    };
    const { calls } = mockApi({
      ...base,
      "/api/system/runner/chat/show": answered,
      "/api/system/runner/chat/confirm": done,
    });
    const user = userEvent.setup();
    const { container } = renderAt("/doctor");
    const card = (await screen.findByRole("heading", { name: "The agent proposes" })).closest("li") as HTMLElement;
    expect(within(card).getByText(proposal.command)).toBeInTheDocument();
    expect(calls.some((request) => request.url.includes("/chat/confirm"))).toBe(false);
    await expectAccessible(container);
    await user.click(within(card).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText(`Ran \`${proposal.command}\`: {}`)).toBeInTheDocument();
    const confirm = calls.find((request) => request.url.includes("/api/system/runner/chat/confirm"));
    expect(confirm?.method).toBe("POST");
    expect(new URL(confirm?.url ?? "").searchParams.get("action")).toBe("a1");
    expect(screen.queryByRole("heading", { name: "The agent proposes" })).not.toBeInTheDocument();
  });

  it("declines a proposed action without running it", async () => {
    sessionStorage.setItem("hz.chat", answered.id);
    const declined = { ...answered, actions: [{ ...proposal, status: "declined" as const }] };
    const { calls } = mockApi({
      ...base,
      "/api/system/runner/chat/show": answered,
      "/api/system/runner/chat/decline": declined,
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "Decline" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "The agent proposes" })).not.toBeInTheDocument());
    expect(calls.some((request) => request.url.includes("/chat/decline"))).toBe(true);
    expect(calls.some((request) => request.url.includes("/chat/confirm"))).toBe(false);
  });

  it("opens a chat about a notification event from its link", async () => {
    const about = { ...empty, context: { kind: "event" as const, ref: "ev42", detail: "" } };
    const { calls } = mockApi({
      ...base,
      "/api/system/runner/chat/open": about,
      "/api/system/runner/chat/show": about,
    });
    renderAt("/?chat-context=event:ev42");
    expect(await screen.findByText("About: event ev42")).toBeInTheDocument();
    const open = calls.find((request) => request.url.includes("/chat/open"));
    expect(new URL(open?.url ?? "").searchParams.get("context")).toBe("event:ev42");
  });

  it("says when a run was stopped for its budget", async () => {
    const stopped = {
      ...empty,
      messages: [
        msg({ role: "user", text: "Write a novel" }),
        msg({ role: "assistant", text: "", status: "budget", detail: "over $0.25", cost_usd: 0.25 }),
      ],
    };
    mockWithStream({ ...base, "/api/system/runner/chat/open": empty }, [frame("result", { data: stopped })]);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.type(screen.getByLabelText("Your message"), "Write a novel{Enter}");
    expect(await screen.findByText("Stopped: over $0.25")).toBeInTheDocument();
  });

  it("shows the fix of a run that failed, as a command to copy", async () => {
    const failed = {
      ...empty,
      messages: [
        msg({ role: "user", text: "Hello" }),
        msg({
          role: "assistant",
          text: "",
          status: "failed",
          detail: "Claude Code isn't logged in: run `claude auth login`",
          fix: "claude auth login",
        }),
      ],
    };
    mockWithStream({ ...base, "/api/system/runner/chat/open": empty }, [frame("result", { data: failed })]);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.type(screen.getByLabelText("Your message"), "Hello{Enter}");
    expect(await screen.findByText(/isn't logged in/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy the command claude auth login" })).toBeInTheDocument();
  });

  it("shows the error a stream ends with", async () => {
    mockWithStream({ ...base, "/api/system/runner/chat/open": empty }, [
      frame("error", { error: "This chat has cost $2.00 of its $2.00 cap" }),
    ]);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.type(screen.getByLabelText("Your message"), "Hello{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("of its $2.00 cap");
  });
});

describe("ChatPanel, mid-answer", () => {
  it("shows the text and the tools as they stream, before the answer is done", async () => {
    const { release } = mockWithHeldStream(
      { ...base, "/api/system/runner/chat/open": empty },
      [frame("tool", { name: "mcp__hz__system_doctor" }), frame("text", { text: "Two checks " })],
      [frame("result", { data: answered })],
    );
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.type(screen.getByLabelText("Your message"), "How healthy?{Enter}");
    const text = await screen.findByText("Two checks");
    const live = text.closest<HTMLElement>('[role="status"]');
    if (!live) throw new Error("The streaming status region should contain the partial answer");
    expect(within(live).getByText("Calling: system_doctor")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    release();
    expect(await screen.findByText("I proposed setting your role to lead.")).toBeInTheDocument();
    expect(screen.queryByText("Calling: system_doctor")).not.toBeInTheDocument();
  });

  it("resumes the session a link names", async () => {
    const { calls } = mockApi({ ...base, "/api/system/runner/chat/show": answered });
    renderAt(`/doctor?chat=${answered.id}`);
    expect(await screen.findByText("I proposed setting your role to lead.")).toBeInTheDocument();
    const show = calls.find((request) => request.url.includes("/chat/show"));
    expect(new URL(show?.url ?? "").searchParams.get("session")).toBe(answered.id);
    expect(sessionStorage.getItem("hz.chat")).toBe(answered.id);
  });

  it("starts afresh when hz no longer keeps the remembered session", async () => {
    sessionStorage.setItem("hz.chat", "fedcba9876543210");
    mockApi({ ...base, "/api/system/runner/chat/show": { status: 400, body: { error: "There is no chat session" } } });
    renderAt("/doctor");
    await waitFor(() => expect(sessionStorage.getItem("hz.chat")).toBeNull());
  });

  it("shows the exact arguments of a proposed action", async () => {
    sessionStorage.setItem("hz.chat", answered.id);
    mockApi({ ...base, "/api/system/runner/chat/show": answered });
    renderAt("/doctor");
    expect(await screen.findByText(/"key": "org.role"/)).toBeInTheDocument();
  });

  it("puts a message back in the composer when sending it failed", async () => {
    mockWithStream({ ...base, "/api/system/runner/chat/open": empty }, [frame("error", { error: "busy" })]);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.type(screen.getByLabelText("Your message"), "Hello{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("busy");
    expect(screen.getByLabelText("Your message")).toHaveValue("Hello");
  });

  it("counts an answer without a reported cost at its cap", async () => {
    sessionStorage.setItem("hz.chat", answered.id);
    const uncounted = {
      ...answered,
      actions: [],
      messages: [msg({ role: "assistant", text: "Done.", runner: "command", cost_usd: null, budget_usd: 0.25 })],
    };
    mockApi({ ...base, "/api/system/runner/chat/show": uncounted });
    renderAt("/doctor");
    await screen.findByText("Done.");
    expect(screen.getByText("cost not reported, counted as $0.25")).toBeInTheDocument();
  });
});

describe("parseEvents", () => {
  it("returns complete events and keeps a partial one for the next chunk", () => {
    const { events, rest } = parseEvents('event: text\ndata: {"text":"Hi"}\n\nevent: result\ndata: {"da');
    expect(events).toEqual([{ event: "text", data: { text: "Hi" } }]);
    expect(rest).toBe('event: result\ndata: {"da');
  });
});

it("keeps a meeting-reminder request in workshop chat without silently creating a fixed check-in", async () => {
  const { calls } = mockWithStream({ ...base, "/api/system/runner/chat/open": empty }, [
    frame("result", { data: answered }),
  ]);
  const user = userEvent.setup();
  renderAt("/doctor");
  const message = "Create an agent that checks my upcoming meetings every five minutes and reminds me";
  await user.type(await screen.findByLabelText("Your message"), `${message}{Enter}`);
  await waitFor(() =>
    expect(calls.some((request) => request.url.includes("/api/stream/system/runner/chat/send"))).toBe(true),
  );
  const sent = calls.find((request) => request.url.includes("/api/stream/system/runner/chat/send"));
  expect(new URL(sent?.url ?? "").searchParams.get("message")).toBe(message);
  expect(
    calls.some(
      (request) => request.url.includes("/heinzel/new-chat") || request.url.includes("/heinzel/propose-check-in"),
    ),
  ).toBe(false);
  expect(screen.getByRole("button", { name: "Create a helper and take this message" })).toBeInTheDocument();
});
