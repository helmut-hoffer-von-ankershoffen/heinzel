import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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
  it.each([
    "Please schedule a check-in every five minutes.",
    "Notify me every 5 minutes.",
    "Send me a notification every five minutes.",
  ])("asks the configured agent for a scoped proposal and keeps activation separate: %s", async (message) => {
    let stored = { ...snapshot, schedule: null as null | typeof proposedSchedule };
    let confirmed = false;
    const agentChat = {
      id: "abcdef0123456789",
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
    const chat = { id: "abcdef0123456789", title: "Check-in conversation", messages: [], actions: [], costs: {} };
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
    expect(panel.getByText(/Ask naturally about the five-minute check-in/)).toBeInTheDocument();
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
    expect(await panel.findByRole("alert")).toHaveTextContent("The schedule request could not be confirmed");
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
    expect(await panel.findByRole("alert")).toHaveTextContent("hz system daemon restart");
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
});
