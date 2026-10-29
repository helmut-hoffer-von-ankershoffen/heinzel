import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { answered, at, base, empty, frame, mockWithHeldStream } from "@/test/chat";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { ago, switcherGroups } from "./SessionSwitcher";

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

const sessions = [
  { id: "aaaaaaaaaaaaaaaa", title: "Make me the lead", messages: 2, pending: 1, spent_usd: 0.0123, updated: at },
  { id: "bbbbbbbbbbbbbbbb", title: "Why is doctor red?", messages: 4, pending: 0, spent_usd: 0.05, updated: at },
];

describe("The chat's session switcher", () => {
  it("lists the Workshop and the recent chats, searchable, and continues the one chosen", async () => {
    const { calls } = mockApi({
      ...base,
      "/api/system/runner/chat/list": sessions,
      "/api/system/runner/chat/show": answered,
    });
    const user = userEvent.setup();
    const { container } = renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "New chat: switch chats" }));
    const list = screen.getByRole("dialog", { name: "Your chats" });
    const recent = await within(list).findByRole("region", { name: "Recent chats" });
    expect(within(recent).getByRole("button", { name: /Why is doctor red\?/ })).toHaveTextContent("4 messages");
    expect(within(list).getByRole("region", { name: "Start" })).toHaveTextContent("Workshop");
    await expectAccessible(container);
    await user.type(within(list).getByRole("searchbox", { name: "Search chats" }), "lead");
    expect(within(list).queryByText("Why is doctor red?")).not.toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: /Make me the lead/ }));
    await waitFor(() =>
      expect(calls.some((call) => new URL(call.url).searchParams.get("session") === "aaaaaaaaaaaaaaaa")).toBe(true),
    );
    expect(sessionStorage.getItem("hz.chat")).toBe("aaaaaaaaaaaaaaaa");
  });

  it("opens the Workshop as a chat about nothing in particular, and starts a new chat from the header", async () => {
    const { calls } = mockApi({
      ...base,
      "/api/system/runner/chat/list": sessions,
      "/api/system/runner/chat/open": { ...empty, context: null, title: "Workshop" },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "New chat: switch chats" }));
    await user.click(
      within(screen.getByRole("dialog", { name: "Your chats" })).getByRole("button", { name: /^Workshop/ }),
    );
    await waitFor(() => expect(calls.some((call) => call.url.includes("/chat/open"))).toBe(true));
    const open = calls.find((call) => call.url.includes("/chat/open"));
    expect(new URL(open?.url ?? "").searchParams.get("context")).toBeNull();
    // Still empty, so still a new chat: the + only puts you in its composer.
    const fresh = screen.getByRole("button", { name: "New chat" });
    expect(fresh).toHaveAttribute("title", "This is a new chat");
    await user.click(fresh);
    expect(screen.getByLabelText("Your message")).toHaveFocus();
    expect(sessionStorage.getItem("hz.chat")).toBe(empty.id);
  });

  it("names a chat by its first message, and starts a new one from the header", async () => {
    sessionStorage.setItem("hz.chat", answered.id);
    mockApi({ ...base, "/api/system/runner/chat/show": { ...answered, title: "Make me the lead" } });
    const user = userEvent.setup();
    renderAt("/doctor");
    expect(await screen.findByRole("button", { name: "Make me the lead: switch chats" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(sessionStorage.getItem("hz.chat")).toBeNull();
    expect(screen.getByRole("button", { name: "New chat: switch chats" })).toBeInTheDocument();
    expect(screen.getByLabelText("Your message")).toHaveFocus();
  });

  it("starts a new chat while an answer is written, leaving that turn to finish in its own session", async () => {
    sessionStorage.setItem("hz.chat", answered.id);
    const { release } = mockWithHeldStream(
      { ...base, "/api/system/runner/chat/show": answered },
      [frame("text", { text: "Thinking it through" })],
      [frame("result", { data: answered })],
    );
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.type(await screen.findByLabelText("Your message"), "And now?{Enter}");
    expect(await screen.findByText("Thinking it through")).toBeInTheDocument();
    const fresh = screen.getByRole("button", { name: "New chat" });
    expect(fresh).toBeEnabled();
    await user.click(fresh);
    expect(screen.queryByText("Thinking it through")).not.toBeInTheDocument();
    release();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(sessionStorage.getItem("hz.chat")).toBeNull(); // the finished turn didn't pull the view back
    expect(screen.getByRole("button", { name: "New chat: switch chats" })).toBeInTheDocument();
  });

  it("says what a message may cost in one line, the rest in its info", async () => {
    mockApi(base);
    renderAt("/doctor");
    expect(await screen.findByText(/^Up to \$0\.25 per message · this chat \$0\.00$/)).toBeInTheDocument();
    expect(screen.getByTitle(/Runs your agent runner and may cost money/)).toBeInTheDocument();
  });

  it("groups and words what it lists", () => {
    expect(switcherGroups(sessions, "doctor").map((group) => group.label)).toEqual(["Recent chats"]);
    expect(ago("2026-10-24T08:00:00Z", Date.parse("2026-10-24T10:00:00Z"))).toBe("2 hours ago");
  });
});

it("creates a personal Heinzel from the chat and opens its durable session", async () => {
  const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
  const saved = {
    session_id: id,
    heinzel_id: id,
    name: "New Heinzel",
    messages: [],
    schedule: null,
    contracts: [],
    appearance: { age: "grown", build: "regular" },
    profile_revision: 1,
  };
  const { calls } = mockApi({
    ...base,
    "/api/system/runner/chat/list": [],
    "/api/heinzel/new-chat": saved,
    "/api/heinzel/session": saved,
    "/api/heinzel/latest-scheduled-agent-proposal": null,
  });
  const user = userEvent.setup();
  renderAt("/doctor");
  await user.click(await screen.findByRole("button", { name: "New chat: switch chats" }));
  await user.click(
    within(screen.getByRole("dialog", { name: "Your chats" })).getByRole("button", {
      name: "Create a personal Heinzel",
    }),
  );
  expect(await screen.findByText("New Heinzel")).toBeInTheDocument();
  const created = calls.find((call) => new URL(call.url).pathname === "/api/heinzel/new-chat");
  expect(await created?.clone().json()).toMatchObject({ name: "New Heinzel", request_id: expect.any(String) });
  expect(localStorage.getItem("hz.personal-heinzel.creation")).toBeNull();
});

it("opens an existing personal Heinzel's continuous chat from the switcher", async () => {
  const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
  const saved = {
    session_id: id,
    heinzel_id: id,
    name: "Atlas",
    messages: [],
    schedule: null,
    contracts: [],
    appearance: { age: "grown", build: "regular" },
    profile_revision: 1,
  };
  mockApi({
    ...base,
    "/api/system/runner/chat/list": [],
    "/api/heinzel/sessions": { sessions: [{ session_id: id, name: "Atlas", state: "proposed" }] },
    "/api/heinzel/session": saved,
  });
  const user = userEvent.setup();
  renderAt("/doctor");
  await user.click(await screen.findByRole("button", { name: "New chat: switch chats" }));
  await user.click(
    await within(screen.getByRole("region", { name: "Your Heinzels" })).findByRole("button", { name: /Atlas/ }),
  );
  expect(await screen.findByRole("heading", { name: "Atlas" })).toBeInTheDocument();
});
