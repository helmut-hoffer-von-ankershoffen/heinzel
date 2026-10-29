import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
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
  expect(screen.getByText(/Creating one through conversation is not available yet/)).toBeInTheDocument();
});
