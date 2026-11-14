import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Providers, queryClient } from "@/App";
import type { SchemaSelfChatStatus } from "@/api/client";
import { expectAccessible, mockApi } from "@/test/render";
import { AnsweredInChat, ChatChannelSettings, connectorName } from "./ChatChannelSettings";

const both: SchemaSelfChatStatus = {
  adapter: "slack",
  notifications: { supported: true, on: false },
  questions: { supported: true, on: false },
  wait_minutes: 5,
  detail: "slack can carry notifications and questions.",
};

function show(initial: Partial<SchemaSelfChatStatus> = {}) {
  let status: SchemaSelfChatStatus = { ...both, ...initial };
  const { calls } = mockApi({
    "/api/system/notify/chat": () => status,
    "/api/system/config/set": (request: Request) => {
      const query = new URL(request.url).searchParams;
      const use = query.get("key") === "system.notify.chat_questions" ? "questions" : "notifications";
      status = { ...status, [use]: { ...status[use], on: query.get("value") === "true" } };
      return { key: query.get("key"), value: query.get("value"), scope: "user" };
    },
  });
  const view = render(
    <Providers client={queryClient()}>
      <ChatChannelSettings />
    </Providers>,
  );
  return { ...view, calls, user: userEvent.setup() };
}

describe("ChatChannelSettings", () => {
  it("offers both switches with the connector that will be used, both off by default", async () => {
    const { container } = show();
    expect(await screen.findByText(/Through Slack, in your own direct messages/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Notices also in chat" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("switch", { name: "Questions also in chat" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/after 5 min it stays open here/)).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("switches questions on in the user configuration", async () => {
    const { calls, user } = show();
    await user.click(await screen.findByRole("switch", { name: "Questions also in chat" }));
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Questions also in chat" })).toHaveAttribute("aria-checked", "true"),
    );
    const set = calls.find((call) => new URL(call.url).pathname === "/api/system/config/set");
    expect(set && Object.fromEntries(new URL(set.url).searchParams)).toEqual({
      key: "system.notify.chat_questions",
      value: "true",
      scope: "user",
    });
  });

  it("offers only what the connector can carry", async () => {
    show({ questions: { supported: false, on: false } });
    expect(await screen.findByRole("switch", { name: "Notices also in chat" })).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "Questions also in chat" })).not.toBeInTheDocument();
  });

  it("offers no switch without a connector that can message you, and says what to do", async () => {
    show({
      adapter: null,
      notifications: { supported: false, on: true },
      questions: { supported: false, on: false },
      detail: "No chat connector that can message you is set up.",
    });
    expect(await screen.findByText("No chat connector that can message you is set up.")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});

describe("AnsweredInChat", () => {
  it("names the connector the answer came from", async () => {
    mockApi({ "/api/system/notify/chat": both });
    render(
      <Providers client={queryClient()}>
        <AnsweredInChat />
      </Providers>,
    );
    expect(await screen.findByText("Answered in Slack")).toBeInTheDocument();
  });

  it("says chat while it can't tell which", () => {
    mockApi({});
    render(
      <Providers client={queryClient()}>
        <AnsweredInChat />
      </Providers>,
    );
    expect(screen.getByText("Answered in chat")).toBeInTheDocument();
  });

  it("writes a connector's name as people say it", () => {
    expect(connectorName("slack")).toBe("Slack");
    expect(connectorName("team-chat")).toBe("Team chat");
  });
});
