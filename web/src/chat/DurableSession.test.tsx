import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { daemonStatusKey } from "@/api/daemon";
import { DurableSession } from "@/chat/DurableSession";
import { frame, mockWithStream } from "@/test/chat";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { preserveUpdateWork } from "@/updates/work";

const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const snapshot = {
  session_id: sessionId,
  name: "Personal helper",
  messages: [{ id: "m1", seq: 1, role: "user", text: "Hello", status: "pending" }],
};

const proposedSchedule = {
  summary: "Five-minute check-in",
  cron: "*/5 * * * *",
  timezone: "Europe/Berlin",
  status: "proposed",
  contract_version: 1,
  revision: 1,
  preview_hash: "a".repeat(64),
};

const reviewChat = {
  id: "0123456789abcdef",
  personal_session_id: sessionId,
  title: "Check-in approval",
  messages: [],
  actions: [
    {
      id: "a1",
      tool: "heinzel_schedule_control",
      module: "heinzel",
      operation: "schedule-control",
      command: "hz heinzel schedule-control",
      arguments: {
        session_id: sessionId,
        request_id: "approval-request",
        expected_revision: 1,
        action: "activate",
        expected_contract_version: 1,
        expected_preview_hash: "a".repeat(64),
      },
      status: "pending",
      mcp_authorized: false,
    },
  ],
  costs: { spent_usd: 0, session_max_usd: 2, message_max_usd: 0.2 },
};
const approvedChat = {
  ...reviewChat,
  actions: [{ ...reviewChat.actions[0], status: "done", result: "schedule active" }],
  messages: [{ role: "action", text: "Ran `hz heinzel schedule-control`: schedule active" }],
};

afterEach(() => sessionStorage.clear());

describe("personal conversation links", () => {
  it("shows an unsupported owner-bound model policy without permitting approval", async () => {
    const review = {
      request_id: "policy-one",
      revision: 1,
      access: "pydantic-ai",
      model: "openai:example",
      requests: 1,
      total_tokens: 100,
      output_tokens: 10,
      timeout_seconds: 10,
      message_cap_usd: "1",
      session_cap_usd: "2",
      owner_cap_usd: "3",
      allowed_tools: {},
      allowed_skill_tools: [],
    };
    const chat = {
      ...reviewChat,
      actions: [
        {
          ...reviewChat.actions[0],
          operation: "approve-model-grant",
          status: "pending",
          arguments: { session_id: sessionId, request_id: "policy-one", review },
        },
      ],
    };
    const { calls } = mockApi({ ...shell, "/api/heinzel/session": snapshot, "/api/system/runner/chat/show": chat });
    renderAt(`/heinzel?chat=${sessionId}&agent-chat=${chat.id}&approval-chat=${chat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    const card = within(await panel.findByRole("region", { name: "Model policy review" }));
    expect(panel.getAllByRole("region", { name: "Model policy review" })).toHaveLength(1);
    expect(card.getByText(/Confirmation is unavailable for this model policy/)).toBeInTheDocument();
    expect(card.getByText(/openai:example/)).toHaveTextContent('"request_id": "policy-one"');
    expect(card.queryByRole("button")).not.toBeInTheDocument();
    expect(calls.filter((request) => request.method === "POST")).toHaveLength(0);
  });

  it("confirms only the exact current bound policy after explicit review", async () => {
    const review = {
      request_id: "policy-one",
      revision: 1,
      access: "pydantic-ai",
      model: "openai:gpt-4.1-mini-2025-04-14",
      binding: {
        owner: `u-${"a".repeat(32)}`,
        access: "pydantic-ai",
        model: "openai:gpt-4.1-mini-2025-04-14",
        endpoint: "https://api.openai.com/v1",
        credential_fingerprint: "b".repeat(64),
      },
      requests: 1,
      total_tokens: 100,
      output_tokens: 10,
      timeout_seconds: 10,
      message_cap_usd: "1",
      session_cap_usd: "2",
      owner_cap_usd: "3",
      allowed_tools: {},
      allowed_skill_tools: [],
    };
    const action = {
      ...reviewChat.actions[0],
      operation: "approve-model-grant",
      status: "pending",
      arguments: { session_id: sessionId, request_id: "policy-one", review },
    };
    const chat = { ...reviewChat, actions: [action] };
    const confirmed = { ...chat, actions: [{ ...action, status: "done" }] };
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": snapshot,
      "/api/system/runner/chat/show": chat,
      "/api/heinzel/model-policy": {
        session_id: sessionId,
        status: "proposed",
        billing: "api-priced",
        proposal: review,
      },
      "/api/system/runner/chat/confirm": confirmed,
    });
    renderAt(`/heinzel?chat=${sessionId}&agent-chat=${chat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    const card = within(await panel.findByRole("region", { name: "Model policy review" }));
    expect(card.getByText(/not a provider account/)).toBeInTheDocument();
    expect(calls.filter((request) => request.method === "POST")).toHaveLength(0);
    fireEvent.click(card.getByRole("button", { name: "Confirm model policy" }));
    await waitFor(() => expect(calls.filter((request) => request.method === "POST")).toHaveLength(1));
    const post = calls.find((request) => request.method === "POST");
    expect(new URL(post?.url ?? "").pathname).toBe("/api/system/runner/chat/confirm");
    await waitFor(() => expect(panel.queryByRole("region", { name: "Model policy review" })).not.toBeInTheDocument());
  });

  it("does not confirm if the reviewed binding changed", async () => {
    const review = {
      request_id: "policy-one",
      revision: 1,
      access: "pydantic-ai",
      model: "openai:gpt-4.1-mini-2025-04-14",
      binding: {
        owner: `u-${"a".repeat(32)}`,
        access: "pydantic-ai",
        model: "openai:gpt-4.1-mini-2025-04-14",
        endpoint: "https://api.openai.com/v1",
        credential_fingerprint: "b".repeat(64),
      },
      allowed_tools: {},
      allowed_skill_tools: [],
    };
    const action = {
      ...reviewChat.actions[0],
      operation: "approve-model-grant",
      status: "pending",
      arguments: { session_id: sessionId, request_id: "policy-one", review },
    };
    const chat = { ...reviewChat, actions: [action] };
    const changed = { ...review, binding: { ...review.binding, credential_fingerprint: "c".repeat(64) } };
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": snapshot,
      "/api/system/runner/chat/show": chat,
      "/api/heinzel/model-policy": {
        session_id: sessionId,
        status: "proposed",
        billing: "api-priced",
        proposal: changed,
      },
    });
    renderAt(`/heinzel?chat=${sessionId}&agent-chat=${chat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    const card = within(await panel.findByRole("region", { name: "Model policy review" }));
    fireEvent.click(card.getByRole("button", { name: "Confirm model policy" }));
    await waitFor(() =>
      expect(calls.some((request) => new URL(request.url).pathname === "/api/heinzel/model-policy")).toBe(true),
    );
    await waitFor(() => expect(panel.getAllByRole("alert").length).toBeGreaterThan(0));
    expect(calls.filter((request) => request.method === "POST")).toHaveLength(0);
  });

  it("does not show another agent's policy action in an otherwise bound chat", async () => {
    const chat = {
      ...reviewChat,
      actions: [
        {
          ...reviewChat.actions[0],
          operation: "approve-model-grant",
          status: "pending",
          arguments: {
            session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW",
            request_id: "foreign",
            review: { request_id: "foreign" },
          },
        },
      ],
    };
    mockApi({ ...shell, "/api/heinzel/session": snapshot, "/api/system/runner/chat/show": chat });
    renderAt(`/heinzel?chat=${sessionId}&agent-chat=${chat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    await panel.findByText("Hello");
    await waitFor(() => expect(panel.queryByRole("region", { name: "Model policy review" })).not.toBeInTheDocument());
  });

  it("pages to newer queued messages and back without presenting the first page as complete", async () => {
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": (request: Request) =>
        new URL(request.url).searchParams.get("cursor") === "opaque-page-51"
          ? {
              ...snapshot,
              messages: [{ id: "new", seq: 52, role: "user", text: "A newer queued message", status: "pending" }],
              has_more: false,
              next_cursor: null,
            }
          : { ...snapshot, has_more: true, next_cursor: "opaque-page-51" },
    });
    renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByText("Hello")).toBeInTheDocument();
    expect(panel.getByRole("navigation", { name: "Conversation history" })).toHaveTextContent("More messages follow");
    fireEvent.click(panel.getByRole("button", { name: "Newer messages" }));
    expect(await panel.findByText("A newer queued message")).toBeInTheDocument();
    expect(panel.queryByText("Hello")).not.toBeInTheDocument();
    fireEvent.click(panel.getByRole("button", { name: "Earlier messages" }));
    expect(await panel.findByText("Hello")).toBeInTheDocument();
    expect(calls.some((request) => new URL(request.url).searchParams.get("cursor") === "opaque-page-51")).toBe(true);
  });

  it("keeps one artifact and skill section across repeated session refreshes", async () => {
    const client = queryClient();
    mockApi({
      "/api/heinzel/session": snapshot,
      "/api/heinzel/personal-artifacts": { session_id: sessionId, artifacts: [] },
    });
    const router = createMemoryRouter(
      [{ path: "/", element: <DurableSession sessionId={sessionId} onClose={() => {}} /> }],
      { initialEntries: ["/"] },
    );
    render(
      <Providers client={client}>
        <RouterProvider router={router} />
      </Providers>,
    );
    await screen.findByText("Hello");
    for (let refresh = 0; refresh < 3; refresh++) {
      await act(async () => {
        await client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
      });
    }
    expect(screen.getAllByRole("heading", { name: "Associated files" })).toHaveLength(1);
    expect(screen.getAllByText("Skill draft", { exact: true })).toHaveLength(1);
  });

  it("offers explicit inactive setup after a definitive pre-acceptance runner rejection", async () => {
    let stored = { ...snapshot, schedule: null as null | typeof proposedSchedule };
    const chat = { ...reviewChat, actions: [] };
    mockApi({
      ...shell,
      "/api/heinzel/session": () => stored,
      "/api/system/runner/chat/open-check-in": chat,
      "/api/system/runner/chat/show": chat,
      "/api/stream/system/runner/chat/send": {
        status: 400,
        body: { error: "This personal agent cannot run until its runner isolation and grants are verified" },
      },
      "/api/heinzel/propose-check-in": () => {
        stored = { ...stored, schedule: proposedSchedule };
        return stored;
      },
    });
    renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    const box = await panel.findByLabelText("Message your Heinzel");
    fireEvent.change(box, { target: { value: "notify me every 5 minutes" } });
    fireEvent.click(panel.getByRole("button", { name: "Prepare schedule review" }));
    const propose = await panel.findByRole("button", { name: "Propose check-in schedule" });
    await waitFor(() => expect(propose).toBeEnabled());
    expect(box).toHaveValue("notify me every 5 minutes");
    expect(stored.schedule).toBeNull();
    fireEvent.click(propose);
    expect(await panel.findByText("proposed", { exact: true })).toBeInTheDocument();
    expect(panel.getByRole("button", { name: "Review activation in chat" })).toBeInTheDocument();
  });

  it.each([null, "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW"])(
    "does not display or send to a linked conversation with binding %s",
    async (binding) => {
      const linked = {
        ...reviewChat,
        personal_session_id: binding,
        messages: [{ role: "assistant", text: "Another agent's private conversation" }],
      };
      const { calls } = mockApi({
        ...shell,
        "/api/heinzel/session": snapshot,
        "/api/system/runner/chat/show": linked,
      });
      renderAt(`/heinzel?chat=${sessionId}&agent-chat=${linked.id}&approval-chat=${linked.id}`);
      const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
      const box = await panel.findByLabelText("Message your Heinzel");
      fireEvent.change(box, { target: { value: "Keep this with the selected agent" } });
      await waitFor(() => expect(panel.getByRole("button", { name: "Queue message" })).toBeDisabled());
      fireEvent.submit(box.closest("form") as HTMLFormElement);
      await waitFor(() => expect(panel.getAllByRole("alert").length).toBeGreaterThan(0));
      expect(panel.queryByText("Another agent's private conversation")).not.toBeInTheDocument();
      expect(panel.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
      expect(calls.some((request) => request.method === "POST")).toBe(false);
      expect(box).toHaveValue("Keep this with the selected agent");
    },
  );

  it("does not render another agent's cached conversation while its link is being checked", async () => {
    const client = queryClient();
    client.setQueryData(["system", "runner", "chat", "show", reviewChat.id], {
      ...reviewChat,
      personal_session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW",
      messages: [{ role: "assistant", text: "Cached private conversation" }],
    });
    mockApi({ ...shell, "/api/heinzel/session": snapshot });
    const router = createMemoryRouter(
      [{ path: "/", element: <DurableSession sessionId={sessionId} onClose={() => {}} /> }],
      { initialEntries: [`/?agent-chat=${reviewChat.id}`] },
    );
    render(
      <Providers client={client}>
        <RouterProvider router={router} />
      </Providers>,
    );
    expect(screen.queryByText("Cached private conversation")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Queue message" })).toBeDisabled());
  });

  it.each([
    "Please schedule a check-in every five minutes.",
    "Notify me every 5 minutes.",
    "Send me a notification every five minutes.",
  ])("asks the configured agent for a scoped proposal and keeps activation separate: %s", async (message) => {
    let stored = { ...snapshot, schedule: null as null | typeof proposedSchedule };
    let confirmed = false;
    const agentChat = {
      id: "abcdef0123456789",
      personal_session_id: sessionId,
      title: "Check-in conversation",
      context: { kind: "check-in", ref: sessionId, request_id: "open-request", timezone: "Europe/Berlin" },
      messages: [
        { role: "user", text: message, at: "2026-10-28T12:00:00Z", status: "completed", request_id: "open-request" },
        {
          role: "assistant",
          text: "I can prepare the five-minute proposal for your review.",
          at: "2026-10-28T12:00:01Z",
          status: "completed",
          request_id: "open-request",
        },
      ],
      actions: [
        {
          id: "a1",
          tool: "heinzel_propose_check_in",
          module: "heinzel",
          operation: "propose-check-in",
          command: "hz heinzel propose-check-in",
          arguments: { session_id: sessionId, request_id: "contract-request", timezone: "Europe/Berlin" },
          status: "pending",
          mcp_authorized: false,
        },
      ],
      costs: { spent_usd: 0, session_max_usd: 2, message_max_usd: 0.2 },
    };
    const completedProposal = {
      ...agentChat,
      actions: [{ ...agentChat.actions[0], status: "done", result: "The schedule proposal was saved." }],
    };
    const { calls } = mockWithStream(
      {
        ...shell,
        "/api/heinzel/session": () => stored,
        "/api/system/runner/chat/open-check-in": agentChat,
        "/api/system/runner/chat/confirm": () => {
          confirmed = true;
          stored = { ...stored, schedule: proposedSchedule };
          return completedProposal;
        },
      },
      [frame("result", { data: agentChat })],
    );
    renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    fireEvent.change(await panel.findByLabelText("Message your Heinzel"), {
      target: { value: message },
    });
    fireEvent.click(panel.getByRole("button", { name: "Prepare schedule review" }));
    expect(await panel.findByText(/I can prepare the five-minute proposal/i)).toBeInTheDocument();
    expect(await panel.findByRole("heading", { name: "Review this schedule change" })).toBeInTheDocument();
    expect(confirmed).toBe(false);
    expect(calls.some((request) => new URL(request.url).pathname === "/api/heinzel/chat")).toBe(false);
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/confirm"))).toBe(false);
    fireEvent.click(panel.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(confirmed).toBe(true));
    await waitFor(() => expect(panel.getByText("proposed")).toBeInTheDocument());
    expect(panel.getByRole("button", { name: "Review activation in chat" })).toBeInTheDocument();
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/propose-schedule-control"))).toBe(false);
    const opened = calls.find((request) => new URL(request.url).pathname.endsWith("/open-check-in"));
    const sent = calls.find((request) => new URL(request.url).pathname.endsWith("/chat/send"));
    expect(opened && new URL(opened.url).searchParams.get("session_id")).toBe(sessionId);
    expect(sent && new URL(sent.url).searchParams.get("message")).toBe(message);
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/confirm"))).toBe(true);
  });

  it("offers a direct inactive proposal when the configured runner is unavailable", async () => {
    let stored = { ...snapshot, schedule: null as null | typeof proposedSchedule };
    const chat = {
      id: "abcdef0123456789",
      personal_session_id: sessionId,
      title: "Check-in conversation",
      messages: [],
      actions: [],
      costs: {},
    };
    const failedChat = {
      ...chat,
      messages: [
        {
          role: "user",
          text: "notify me every 5 minutes",
          at: "2026-10-28T12:00:00Z",
          request_id: "open-request",
          status: "completed",
        },
        {
          role: "assistant",
          text: "",
          detail: "agent runner is not configured",
          at: "2026-10-28T12:00:01Z",
          request_id: "open-request",
          status: "failed",
        },
      ],
    };
    const { calls } = mockWithStream(
      {
        ...shell,
        "/api/heinzel/session": () => stored,
        "/api/system/runner/chat/open-check-in": chat,
        "/api/system/runner/chat/show": chat,
        "/api/heinzel/propose-check-in": () => {
          stored = { ...stored, schedule: proposedSchedule };
          return stored;
        },
      },
      [frame("result", { data: failedChat })],
    );
    renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    fireEvent.change(await panel.findByLabelText("Message your Heinzel"), {
      target: { value: "notify me every 5 minutes" },
    });
    fireEvent.click(panel.getByRole("button", { name: "Prepare schedule review" }));
    expect(await panel.findByText(/configured agent runner could not answer/i)).toBeInTheDocument();
    fireEvent.click(panel.getByRole("button", { name: "Propose check-in schedule" }));
    await waitFor(() => expect(panel.getByText("proposed")).toBeInTheDocument());
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/propose-schedule-control"))).toBe(false);
  });

  it("recovers an interrupted proposal without replaying it", async () => {
    const action = {
      id: "a1",
      tool: "heinzel_propose_check_in",
      module: "heinzel",
      operation: "propose-check-in",
      command: "hz heinzel propose-check-in",
      arguments: { session_id: sessionId, request_id: "proposal-request", timezone: "Europe/Berlin" },
      status: "running",
      mcp_authorized: false,
    };
    const interruptedChat = {
      id: "abcdef0123456789",
      personal_session_id: sessionId,
      title: "Check-in conversation",
      context: { kind: "check-in", ref: sessionId, request_id: "open-request", timezone: "Europe/Berlin" },
      messages: [],
      actions: [action],
      costs: { spent_usd: 0, session_max_usd: 2, message_max_usd: 0.2 },
    };
    const uncertainChat = {
      ...interruptedChat,
      actions: [
        {
          ...action,
          status: "uncertain",
          result: "The proposal outcome is unknown. It was not repeated.",
        },
      ],
    };
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: null },
      "/api/system/runner/chat/show": interruptedChat,
      "/api/system/runner/chat/recover": uncertainChat,
    });
    renderAt(`/heinzel?chat=${sessionId}&agent-chat=${interruptedChat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    fireEvent.click(await panel.findByRole("button", { name: "Check proposal outcome without rerunning" }));
    expect(await panel.findByText(/proposal outcome is unknown/i)).toBeInTheDocument();
    expect(panel.getByRole("button", { name: "Propose check-in schedule" })).toBeDisabled();
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/confirm"))).toBe(false);
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/heinzel/propose-check-in"))).toBe(false);
  });

  it("reconciles a lost proposal-confirmation acknowledgement from the saved chat", async () => {
    let stored = { ...snapshot, schedule: null as null | typeof proposedSchedule };
    let confirmed = false;
    const pendingChat = {
      id: "abcdef0123456789",
      personal_session_id: sessionId,
      title: "Check-in conversation",
      context: { kind: "check-in", ref: sessionId, request_id: "open-request", timezone: "Europe/Berlin" },
      messages: [],
      actions: [
        {
          id: "a1",
          tool: "heinzel_propose_check_in",
          module: "heinzel",
          operation: "propose-check-in",
          command: "hz heinzel propose-check-in",
          arguments: { session_id: sessionId, request_id: "proposal-request", timezone: "Europe/Berlin" },
          status: "pending",
          mcp_authorized: false,
        },
      ],
      costs: { spent_usd: 0, session_max_usd: 2, message_max_usd: 0.2 },
    };
    const doneChat = {
      ...pendingChat,
      actions: [{ ...pendingChat.actions[0], status: "done", result: "The schedule proposal was saved." }],
    };
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": () => stored,
      "/api/system/runner/chat/show": () => (confirmed ? doneChat : pendingChat),
      "/api/system/runner/chat/confirm": () => {
        confirmed = true;
        stored = { ...stored, schedule: proposedSchedule };
        return { status: 503, body: { detail: "acknowledgement lost" } };
      },
    });
    renderAt(`/heinzel?chat=${sessionId}&agent-chat=${pendingChat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    fireEvent.click(await panel.findByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(panel.getByText("proposed")).toBeInTheDocument());
    expect(calls.filter((request) => new URL(request.url).pathname.endsWith("/chat/confirm"))).toHaveLength(1);
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/propose-schedule-control"))).toBe(false);
  });

  it.each([
    "Please do not check in every five minutes.",
    "Do not notify me every five minutes.",
    "What does ‘check in every five minutes’ mean?",
    "Could you check in every five minutes?",
    "Can you send me a notification every five minutes?",
    "Please check in every ten minutes.",
    "Please check in every five minutes and pause the schedule.",
    "Send me a notification every five minutes unless I say otherwise.",
    "Example: check in every five minutes.",
  ])("does not stage a schedule from non-unambiguous request %s", async (message) => {
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: null },
      "/api/heinzel/chat": async (request: Request) => {
        const body = await request.json();
        return { session_id: sessionId, request_id: body.request_id, disposition: "queued", inbox_position: 1 };
      },
    });
    renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    fireEvent.change(await panel.findByLabelText("Message your Heinzel"), {
      target: { value: message },
    });
    fireEvent.click(panel.getByRole("button", { name: "Queue message" }));
    await waitFor(() =>
      expect(calls.some((request) => new URL(request.url).pathname === "/api/heinzel/chat")).toBe(true),
    );
    expect(calls.some((request) => new URL(request.url).pathname === "/api/heinzel/propose-check-in")).toBe(false);
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/propose-schedule-control"))).toBe(false);
  });

  it("opens the owned durable session in the chat panel even when it was closed", async () => {
    localStorage.setItem("hz.chat.open", "false");
    const { calls } = mockApi({ ...shell, "/api/heinzel/session": snapshot });
    const { container } = renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByRole("heading", { name: "Personal helper" })).toBeInTheDocument();
    expect(panel.getByText("Hello")).toBeInTheDocument();
    expect(panel.getByText(/Messages are queued here/)).toBeInTheDocument();
    expect(calls.some((request) => new URL(request.url).pathname === "/api/system/runner/chat/show")).toBe(false);
    expect(calls.some((request) => new URL(request.url).searchParams.get("session_id") === sessionId)).toBe(true);
    await expectAccessible(container);
  });

  it("queues a message and retries an uncertain acknowledgement with identical input", async () => {
    const sent: unknown[] = [];
    mockApi({
      ...shell,
      "/api/heinzel/session": snapshot,
      "/api/heinzel/chat": async (request: Request) => {
        const body = await request.json();
        sent.push(body);
        if (sent.length === 1) return { status: 503, body: { detail: "unknown" } };
        return { session_id: sessionId, request_id: body.request_id, disposition: "queued", inbox_position: 2 };
      },
    });
    renderAt(`/heinzel?chat=${sessionId}`);
    const box = await screen.findByLabelText("Message your Heinzel");
    fireEvent.change(box, { target: { value: "A follow-up" } });
    fireEvent.click(screen.getByRole("button", { name: "Queue message" }));
    const retry = await screen.findByRole("button", { name: "Retry the same message" });
    expect(box).toBeDisabled();
    fireEvent.click(retry);
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[1]).toEqual(sent[0]);
    expect(sent[0]).toMatchObject({ session_id: sessionId, message: "A follow-up", when_busy: "follow_up" });
  });

  it("preserves unsent text for an update and restores it after the durable panel remounts", async () => {
    mockApi({ ...shell, "/api/heinzel/session": snapshot });
    const first = renderAt(`/heinzel?chat=${sessionId}`);
    const box = await screen.findByLabelText("Message your Heinzel");
    fireEvent.change(box, { target: { value: "Keep this unsent request" } });
    expect(sessionStorage.getItem(`hz.durable.composer.${sessionId}`)).toContain("Keep this unsent request");
    expect(preserveUpdateWork).not.toThrow();
    first.unmount();

    mockApi({ ...shell, "/api/heinzel/session": snapshot });
    renderAt(`/heinzel?chat=${sessionId}`);
    expect(await screen.findByLabelText("Message your Heinzel")).toHaveValue("Keep this unsent request");
    await waitFor(() => expect(screen.getByRole("button", { name: "Queue message" })).toBeEnabled());
  });

  it("blocks new work when saved composer state is unreadable until the person resolves it", async () => {
    sessionStorage.setItem(`hz.durable.composer.${sessionId}`, "not valid saved state");
    mockApi({ ...shell, "/api/heinzel/session": snapshot });
    renderAt(`/heinzel?chat=${sessionId}`);
    const box = await screen.findByLabelText("Message your Heinzel");
    expect(await screen.findByRole("alert")).toHaveTextContent(/saved composer work.*could not be read/i);
    expect(box).toBeDisabled();
    expect(preserveUpdateWork).toThrow(/could not be preserved/);

    fireEvent.click(
      screen.getByRole("button", { name: "I reviewed the conversation; clear unreadable composer work" }),
    );
    expect(box).toBeEnabled();
    expect(sessionStorage.getItem(`hz.durable.composer.${sessionId}`)).toBeNull();
  });

  it("restores an uncertain request id after reload and retries the exact same request", async () => {
    const firstPayloads: unknown[] = [];
    mockApi({
      ...shell,
      "/api/heinzel/session": snapshot,
      "/api/heinzel/chat": async (request: Request) => {
        firstPayloads.push(await request.json());
        return { status: 503, body: { detail: "acknowledgement lost" } };
      },
    });
    const first = renderAt(`/heinzel?chat=${sessionId}`);
    fireEvent.change(await screen.findByLabelText("Message your Heinzel"), { target: { value: "A follow-up" } });
    fireEvent.click(screen.getByRole("button", { name: "Queue message" }));
    await screen.findByRole("button", { name: "Retry the same message" });
    const saved = JSON.parse(sessionStorage.getItem(`hz.durable.composer.${sessionId}`) ?? "null");
    expect(saved.pending).toMatchObject({ message: "A follow-up" });
    expect(preserveUpdateWork).not.toThrow();
    first.unmount();

    const retriedPayloads: unknown[] = [];
    mockApi({
      ...shell,
      "/api/heinzel/session": snapshot,
      "/api/heinzel/chat": async (request: Request) => {
        retriedPayloads.push(await request.json());
        const body = retriedPayloads[0] as { request_id: string };
        return { session_id: sessionId, request_id: body.request_id, disposition: "queued", inbox_position: 2 };
      },
    });
    renderAt(`/heinzel?chat=${sessionId}`);
    const retry = await screen.findByRole("button", { name: "Retry the same message" });
    await waitFor(() => expect(retry).toBeEnabled());
    fireEvent.click(retry);
    await waitFor(() => expect(retriedPayloads).toHaveLength(1));
    expect(retriedPayloads[0]).toEqual(firstPayloads[0]);
    expect(sessionStorage.getItem(`hz.durable.composer.${sessionId}`)).toBeNull();
  });

  it.each([404, 422])(
    "keeps the runner message and request id after a pre-acceptance %s and reload",
    async (status) => {
      const agentChat = {
        id: "abcdef0123456789",
        personal_session_id: sessionId,
        title: "Check-in conversation",
        context: { kind: "check-in", ref: sessionId, request_id: "open-request", timezone: "Europe/Berlin" },
        messages: [],
        actions: [],
        costs: { spent_usd: 0, session_max_usd: 2, message_max_usd: 0.2 },
      };
      const completed = {
        ...agentChat,
        messages: [
          {
            role: "user",
            text: "Notify me every 5 minutes",
            request_id: "stable-runner-request",
            at: "2026-10-28T12:00:00Z",
            status: "completed",
          },
          {
            role: "assistant",
            text: "I prepared the schedule proposal for review.",
            request_id: "stable-runner-request",
            at: "2026-10-28T12:00:01Z",
            status: "completed",
          },
        ],
      };
      const { calls } = mockApi({
        ...shell,
        "/api/heinzel/session": { ...snapshot, schedule: null },
        "/api/system/runner/chat/open-check-in": agentChat,
        "/api/system/runner/chat/show": agentChat,
      });
      const originalFetch = globalThis.fetch;
      const requestIds: string[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: Request) => {
          if (new URL(input.url).pathname === "/api/stream/system/runner/chat/send") {
            calls.push(input);
            requestIds.push(new URL(input.url).searchParams.get("request_id") ?? "");
            if (requestIds.length === 1) return Response.json({ detail: "runner unavailable" }, { status });
            return new Response(frame("result", { data: completed }), {
              headers: { "Content-Type": "text/event-stream" },
            });
          }
          return originalFetch(input);
        }),
      );

      const first = renderAt(`/heinzel?chat=${sessionId}`);
      const message = "Notify me every 5 minutes";
      fireEvent.change(await screen.findByLabelText("Message your Heinzel"), { target: { value: message } });
      fireEvent.click(screen.getByRole("button", { name: "Prepare schedule review" }));
      await screen.findByText(/configured agent runner could not answer/i);
      expect(await screen.findByLabelText("Message your Heinzel")).toHaveValue(message);
      const saved = JSON.parse(sessionStorage.getItem(`hz.durable.composer.${sessionId}`) ?? "null");
      expect(saved.agentPending.message).toBe(message);
      const requestId = saved.agentPending.request_id;
      first.unmount();

      renderAt(`/heinzel?chat=${sessionId}&agent-chat=${agentChat.id}`);
      expect(await screen.findByLabelText("Message your Heinzel")).toHaveValue(message);
      const retry = await screen.findByRole("button", { name: "Retry the same message" });
      await waitFor(() => expect(retry).toBeEnabled());
      fireEvent.click(retry);
      await waitFor(() => expect(requestIds).toHaveLength(2));
      expect(requestIds).toEqual([requestId, requestId]);
      expect(sessionStorage.getItem(`hz.durable.composer.${sessionId}`)).toBeNull();
    },
  );
  it("opens the durable conversation from a check-in notice", async () => {
    localStorage.setItem("hz.chat.open", "false");
    const notice = {
      id: "20261027T0900-ab12",
      kind: "heinzel",
      title: "Personal helper checked in",
      body: "Scheduled check-in",
      route: `/heinzel?chat=${sessionId}`,
      command: "hz heinzel sessions",
      key: "check-in",
      urgent: false,
      at: "2026-10-27T09:00:00Z",
      due: "2026-10-27T09:00:00Z",
      popup: true,
    };
    mockApi({
      ...shell,
      "/api/heinzel/session": snapshot,
      "/api/system/notify/list": { notices: [notice], waiting: 0, native: "local" },
    });
    renderAt("/doctor?panel=notifications");
    fireEvent.click(await screen.findByRole("button", { name: /Personal helper checked in/ }));
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByRole("heading", { name: "Personal helper" })).toBeInTheDocument();
    expect(panel.getByText("Hello")).toBeInTheDocument();
  });

  it("proposes a schedule and confirms it through the existing chat approval boundary", async () => {
    let proposalCount = 0;
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: proposedSchedule },
      "/api/system/runner/chat/propose-schedule-control": (request: Request) => {
        proposalCount += 1;
        const url = new URL(request.url);
        expect(url.searchParams.get("durable_session")).toBe(sessionId);
        expect(url.searchParams.get("expected_revision")).toBe("1");
        expect(url.searchParams.get("expected_preview_hash")).toBe("a".repeat(64));
        return proposalCount === 1 ? { status: 503, body: { detail: "acknowledgement lost" } } : reviewChat;
      },
      "/api/system/runner/chat/show": reviewChat,
      "/api/system/runner/chat/confirm": (request: Request) => {
        const url = new URL(request.url);
        expect(url.searchParams.get("session")).toBe(reviewChat.id);
        expect(url.searchParams.get("action")).toBe("a1");
        return approvedChat;
      },
    });
    renderAt(`/heinzel?chat=${sessionId}&approval-request=review-request`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByText("*/5 * * * * · Europe/Berlin")).toBeInTheDocument();
    fireEvent.click(panel.getByRole("button", { name: "Review activation in chat" }));
    expect(await panel.findByText(/The schedule request could not be confirmed/)).toBeInTheDocument();
    fireEvent.click(panel.getByRole("button", { name: "Review activation in chat" }));
    await waitFor(() =>
      expect(calls.some((request) => new URL(request.url).pathname.endsWith("/propose-schedule-control"))).toBe(true),
    );
    expect(await panel.findByRole("heading", { name: "Review this schedule change" })).toBeInTheDocument();
    expect(panel.queryByRole("button", { name: "Review activation in chat" })).not.toBeInTheDocument();
    fireEvent.click(panel.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/confirm"))).toBe(true),
    );
    expect(panel.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    const propose = calls.find((request) => new URL(request.url).pathname.endsWith("/propose-schedule-control"));
    expect(propose).toBeDefined();
    if (!propose) throw new Error("The schedule approval proposal was not sent");
    expect(new URL(propose.url).searchParams.get("action")).toBe("activate");
    const proposals = calls.filter((request) => new URL(request.url).pathname.endsWith("/propose-schedule-control"));
    expect(proposals).toHaveLength(2);
    const [firstProposal, secondProposal] = proposals;
    if (!firstProposal || !secondProposal) throw new Error("Both schedule review attempts must be recorded");
    expect(new URL(firstProposal.url).searchParams.get("request_id")).toBe("review-request");
    expect(new URL(secondProposal.url).searchParams.get("request_id")).toBe("review-request");
  });

  it("starts a fresh exact-intent review after a person declines the previous proposal", async () => {
    const declinedChat = {
      ...reviewChat,
      actions: [{ ...reviewChat.actions[0], status: "declined" }],
    };
    const replacementChat = { ...reviewChat, id: "fedcba9876543210" };
    let proposalCount = 0;
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: proposedSchedule },
      "/api/system/runner/chat/propose-schedule-control": () => {
        proposalCount += 1;
        return proposalCount === 1 ? reviewChat : replacementChat;
      },
      "/api/system/runner/chat/decline": declinedChat,
    });
    renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    fireEvent.click(await panel.findByRole("button", { name: "Review activation in chat" }));
    fireEvent.click(await panel.findByRole("button", { name: "Decline" }));
    fireEvent.click(await panel.findByRole("button", { name: "Review activation in chat" }));
    expect(await panel.findByRole("heading", { name: "Review this schedule change" })).toBeInTheDocument();
    const proposalCalls = calls.filter((request) =>
      new URL(request.url).pathname.endsWith("/propose-schedule-control"),
    );
    expect(proposalCalls).toHaveLength(2);
    const [firstProposal, secondProposal] = proposalCalls;
    if (!firstProposal || !secondProposal) throw new Error("The review proposals were not recorded");
    expect(new URL(firstProposal.url).searchParams.get("request_id")).not.toBe(
      new URL(secondProposal.url).searchParams.get("request_id"),
    );
  });

  it("withholds confirmation for a stale schedule review and lets the person discard it", async () => {
    const declinedChat = {
      ...reviewChat,
      actions: [{ ...reviewChat.actions[0], status: "declined" }],
    };
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": {
        ...snapshot,
        schedule: { ...proposedSchedule, revision: 2, preview_hash: "b".repeat(64) },
      },
      "/api/system/runner/chat/show": reviewChat,
      "/api/system/runner/chat/decline": declinedChat,
    });
    renderAt(`/heinzel?chat=${sessionId}&approval-chat=${reviewChat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByText(/schedule changed after this review was created/i)).toBeInTheDocument();
    expect(panel.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    fireEvent.click(panel.getByRole("button", { name: "Discard stale review" }));
    expect(await panel.findByRole("button", { name: "Review activation in chat" })).toBeInTheDocument();
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/confirm"))).toBe(false);
  });

  it("restores the exact pending schedule approval after a page reload", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: proposedSchedule },
      "/api/system/runner/chat/show": reviewChat,
    });
    renderAt(`/heinzel?chat=${sessionId}&approval-chat=${reviewChat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByRole("heading", { name: "Review this schedule change" })).toBeInTheDocument();
    expect(panel.getByRole("button", { name: "Confirm" })).toBeInTheDocument();
  });

  it("lets the person authorize one private MCP run without confirming it in the web request", async () => {
    const authorized = {
      ...reviewChat,
      actions: [{ ...reviewChat.actions[0], mcp_authorized: true }],
    };
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: proposedSchedule },
      "/api/system/runner/chat/show": reviewChat,
      "/api/system/runner/chat/authorize-for-mcp": authorized,
    });
    renderAt(`/heinzel?chat=${sessionId}&approval-chat=${reviewChat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    fireEvent.click(await panel.findByRole("button", { name: "Authorize MCP to run once" }));
    expect(await panel.findByText("Authorized for one MCP run; expires in five minutes")).toBeInTheDocument();
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/authorize-for-mcp"))).toBe(true);
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/confirm"))).toBe(false);
  });

  it("shows the daemon restart requirement when an old installed version blocks check-in approval", async () => {
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: proposedSchedule },
      "/api/system/runner/chat/show": reviewChat,
      "/api/system/runner/chat/authorize-for-mcp": {
        status: 400,
        body: {
          error:
            "Check-ins are blocked because the daemon unit version is 0.7.27 and the last daemon service tick version is 0.7.27; run `hz system daemon restart`, wait for a service-managed tick from the current Heinzel, then review this action again",
        },
      },
    });
    renderAt(`/heinzel?chat=${sessionId}&approval-chat=${reviewChat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    fireEvent.click(await panel.findByRole("button", { name: "Authorize MCP to run once" }));
    expect(await panel.findByText(/hz system daemon restart/)).toBeInTheDocument();
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/confirm"))).toBe(false);
  });

  it("checks an interrupted confirmation without rerunning it and keeps an unknown result visible", async () => {
    const interrupted = {
      ...reviewChat,
      actions: [{ ...reviewChat.actions[0], status: "running" }],
    };
    const unresolved = {
      ...interrupted,
      actions: [
        {
          ...interrupted.actions[0],
          status: "uncertain",
          result: "This confirmation stopped before its result was saved. The action was not repeated.",
        },
      ],
    };
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: proposedSchedule },
      "/api/system/runner/chat/show": interrupted,
      "/api/system/runner/chat/recover": unresolved,
    });
    renderAt(`/heinzel?chat=${sessionId}&approval-chat=${reviewChat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    fireEvent.click(await panel.findByRole("button", { name: "Check outcome without rerunning" }));
    expect(
      await panel.findByText("This confirmation stopped before its result was saved. The action was not repeated."),
    ).toBeInTheDocument();
    expect(panel.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    expect(panel.queryByRole("button", { name: "Review activation in chat" })).not.toBeInTheDocument();
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/confirm"))).toBe(false);
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/recover"))).toBe(true);
    fireEvent.click(panel.getByRole("button", { name: "I checked the schedule; close this recovery note" }));
    expect(await panel.findByRole("button", { name: "Review activation in chat" })).toBeInTheDocument();
  });

  it("retains recovery access when the confirmation acknowledgement is lost while the action is running", async () => {
    let showCount = 0;
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: proposedSchedule },
      "/api/system/runner/chat/show": () => {
        showCount += 1;
        return showCount === 1
          ? reviewChat
          : { ...reviewChat, actions: [{ ...reviewChat.actions[0], status: "running" }] };
      },
      "/api/system/runner/chat/confirm": { status: 503, body: { detail: "acknowledgement lost" } },
    });
    renderAt(`/heinzel?chat=${sessionId}&approval-chat=${reviewChat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    fireEvent.click(await panel.findByRole("button", { name: "Confirm" }));
    expect(await panel.findByRole("button", { name: "Check outcome without rerunning" })).toBeInTheDocument();
    expect(calls.filter((request) => new URL(request.url).pathname.endsWith("/chat/confirm"))).toHaveLength(1);
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/chat/recover"))).toBe(false);
  });

  it("does not leave a completed confirmation in the pending review flow after reload", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: { ...proposedSchedule, status: "active" } },
      "/api/system/runner/chat/show": approvedChat,
    });
    renderAt(`/heinzel?chat=${sessionId}&approval-chat=${reviewChat.id}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByText("active")).toBeInTheDocument();
    expect(panel.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    expect(panel.queryByRole("button", { name: "Check outcome without rerunning" })).not.toBeInTheDocument();
  });

  it("explains when an active schedule is dormant because the daemon is not installed", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: { ...proposedSchedule, status: "active" } },
      "/api/system/daemon/status": {
        executable: null,
        failing: [],
        installed: false,
        installed_version: null,
        interval_minutes: null,
        jobs: 0,
        last_tick: null,
        loaded: false,
        paused: false,
        platform: "darwin",
        version: null,
        version_mismatch: false,
      },
    });
    renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByText(/no background daemon is installed, so check-ins are dormant/i)).toBeInTheDocument();
    expect(panel.getByRole("link", { name: "Open daemon settings" })).toHaveAttribute("href", "/?settings=daemon");
    expect(panel.queryByRole("button", { name: "Install" })).not.toBeInTheDocument();
  });

  it.each([
    ["paused", { paused: true }, /background daemon is paused, so check-ins are dormant/i],
    ["version mismatch", { version_mismatch: true }, /not verified running the installed Heinzel version/i],
    [
      "version mismatch with unknown cadence",
      { version_mismatch: true, interval_minutes: null },
      /not verified running the installed Heinzel version/i,
    ],
    ["slow cadence", { interval_minutes: 10 }, /cadence is unknown or slower than five minutes/i],
    ["unknown cadence", { interval_minutes: null }, /cadence is unknown or slower than five minutes/i],
    ["stale tick", { last_tick: "2000-01-01T00:00:00Z" }, /has not reported a recent valid tick/i],
    ["missing heartbeat", { last_tick: null }, /has not reported a recent valid tick/i],
    ["invalid heartbeat", { last_tick: "not-a-timestamp" }, /has not reported a recent valid tick/i],
    [
      "future heartbeat",
      { last_tick: new Date(Date.now() + 5 * 60_000).toISOString() },
      /has not reported a recent valid tick/i,
    ],
  ])("directs an active check-in to daemon recovery when readiness is %s", async (_case, change, copy) => {
    mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: { ...proposedSchedule, status: "active" } },
      "/api/system/daemon/status": {
        executable: "/usr/local/bin/hz",
        failing: [],
        installed: true,
        installed_version: "1.2.3",
        interval_minutes: 5,
        jobs: 1,
        last_tick: new Date().toISOString(),
        loaded: true,
        paused: false,
        platform: "darwin",
        version: "1.2.3",
        version_mismatch: false,
        ...change,
      },
    });
    renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByText(copy)).toBeInTheDocument();
    expect(panel.getByRole("link", { name: "Open daemon settings" })).toHaveAttribute("href", "/?settings=daemon");
  });

  it("tolerates a small future heartbeat skew without claiming a daemon failure", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: { ...proposedSchedule, status: "active" } },
      "/api/system/daemon/status": {
        executable: "/usr/local/bin/hz",
        failing: [],
        installed: true,
        installed_version: "1.2.3",
        interval_minutes: 5,
        jobs: 1,
        last_tick: new Date(Date.now() + 30_000).toISOString(),
        loaded: true,
        paused: false,
        platform: "darwin",
        version: "1.2.3",
        version_mismatch: false,
      },
    });
    renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByText("active")).toBeInTheDocument();
    expect(panel.queryByRole("link", { name: "Open daemon settings" })).not.toBeInTheDocument();
  });

  it("rechecks readiness as an unchanged daemon heartbeat ages across status polls", async () => {
    let now = Date.parse("2026-10-29T12:00:00Z");
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const observedAt = new Date(now).toISOString();
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/session": { ...snapshot, schedule: { ...proposedSchedule, status: "active" } },
      "/api/system/daemon/status": {
        executable: "/usr/local/bin/hz",
        failing: [],
        installed: true,
        installed_version: "1.2.3",
        interval_minutes: 1,
        jobs: 1,
        last_tick: observedAt,
        loaded: true,
        paused: false,
        platform: "darwin",
        version: "1.2.3",
        version_mismatch: false,
      },
    });
    sessionStorage.setItem("hz.token", "test-token");
    const client = queryClient();
    const router = createMemoryRouter(
      [{ path: "/heinzel", element: <DurableSession sessionId={sessionId} onClose={() => {}} /> }],
      { initialEntries: ["/heinzel"] },
    );
    const view = render(
      <Providers client={client}>
        <RouterProvider router={router} />
      </Providers>,
    );
    try {
      const schedule = within(await screen.findByRole("region", { name: "Five-minute check-in" }));
      expect(await schedule.findByText("active")).toBeInTheDocument();
      await act(async () => {
        await client.refetchQueries({ queryKey: daemonStatusKey });
      });
      expect(schedule.queryByRole("link", { name: "Open daemon settings" })).not.toBeInTheDocument();
      now += 11 * 60_000;
      await act(async () => {
        await client.refetchQueries({ queryKey: daemonStatusKey });
      });
      expect(await schedule.findByText(/has not reported a recent valid tick/i)).toBeInTheDocument();
      expect(
        calls.filter((request) => new URL(request.url).pathname.endsWith("/daemon/status")).length,
      ).toBeGreaterThan(1);
    } finally {
      view.unmount();
      vi.restoreAllMocks();
    }
  });
});

it("shows recorded activity and stored drafts without exposing tool bodies or inventing approval", async () => {
  mockApi({
    ...shell,
    "/api/heinzel/session": {
      ...snapshot,
      appearance: { age: "grown", build: "regular", hat: "red", shirt: "blue", accessories: ["scarf"] },
      profile_revision: 1,
      messages: [{ id: "tool1", seq: 1, role: "tool", text: "private tool payload", status: "failed" }],
      pending_actions: [
        { id: "p1", status: "pending", preview: { title: "Review draft", target: "Report", after: "A draft" } },
      ],
      schedule: null,
    },
  });
  renderAt(`/?chat=${sessionId}`);
  expect((await screen.findByText(/Tool activity:/)).parentElement).toHaveTextContent("failed");
  expect(screen.queryByText("private tool payload")).not.toBeInTheDocument();
  expect(screen.getByText("Waiting for approval", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("Draft: A draft")).toBeInTheDocument();
  expect(screen.getByText("Approval for this durable action is not available here yet.")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Customize your Heinzel"));
  expect(screen.getByRole("checkbox", { name: "scarf" })).toBeChecked();
});

it("shows only owner-session associations and recorded review states in the agent chat", async () => {
  const ref = "https://docs.google.com/document/d/abc123_DEF456/edit";
  const { calls } = mockApi({
    ...shell,
    "/api/heinzel/session": {
      ...snapshot,
      pending_actions: [
        {
          id: "draft-1",
          operation: "publish",
          status: "pending",
          preview: { title: "Weekly notes", after: "Proposed draft" },
        },
        { id: "draft-2", operation: "send", status: "uncertain" },
      ],
      contracts: [{ id: "contract-1", version: 1, title: "Check-in", status: "approved" }],
    },
    "/api/heinzel/personal-artifacts": {
      session_id: sessionId,
      artifacts: [
        {
          session_id: sessionId,
          request_id: "file-1",
          provider: "google",
          kind: "document",
          ref,
          provenance: "principal-submitted",
        },
      ],
    },
  });
  renderAt(`/?chat=${sessionId}`);
  const link = await screen.findByRole("link", { name: /Google Doc/ });
  expect(link).toHaveAttribute("href", ref);
  expect(screen.getByText(/agent management are unverified/)).toBeInTheDocument();
  expect(screen.getByText(/Recent agent edits are not available/)).toBeInTheDocument();
  expect(screen.getByText("Weekly notes")).toBeInTheDocument();
  expect(screen.getByText("Status: Waiting for approval")).toBeInTheDocument();
  expect(screen.getByText("Draft: Proposed draft")).toBeInTheDocument();
  expect(screen.getByText("Status: uncertain")).toBeInTheDocument();
  expect(screen.getByText(/Contract v1: approved/)).toBeInTheDocument();
  const request = calls.find((call) => new URL(call.url).pathname.endsWith("/personal-artifacts"));
  expect(new URL(request?.url ?? "").searchParams.get("session_id")).toBe(sessionId);
});

it("does not display another session's action or request its associations", async () => {
  const other = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
  const { calls } = mockApi({
    ...shell,
    "/api/heinzel/session": {
      ...snapshot,
      session_id: other,
      pending_actions: [{ id: "foreign", operation: "private-action", status: "pending" }],
    },
  });
  renderAt(`/?chat=${sessionId}`);
  expect(await screen.findByText("This personal conversation could not be loaded.")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Personal helper" })).not.toBeInTheDocument();
  expect(screen.queryByText(/private-action/)).not.toBeInTheDocument();
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("/personal-artifacts"))).toBe(false);
});
