// One kind of chat (ADR 0157, bead agqz): every chat is with a Heinzel, there is no general chat and no "Back to chat",
// and the header carries the face, the name with its switcher, More and Close.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { base, empty } from "@/test/chat";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const helmi = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const fritz = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
const at = "2026-11-14T09:00:00Z";
const names: Record<string, string> = { [helmi]: "Helmi", [fritz]: "Fritz" };
const summary = (session_id: string, state = "awake") => ({
  session_id,
  name: names[session_id],
  state,
  last_seq: 1,
  updated_at: at,
});
const snapshot = (request: Request) => {
  const id = new URL(request.url).searchParams.get("session_id") ?? "";
  return { session_id: id, heinzel_id: id, name: names[id] ?? "New", messages: [] };
};
function mockOneChat(sessions: object[], extra: Record<string, unknown> = {}) {
  return mockApi({
    ...base,
    "/api/heinzel/sessions": { sessions },
    "/api/heinzel/session": snapshot,
    ...extra,
  });
}

/** The element's nearest ancestor matching `selector`; a missing one fails the test here, not later. */
function inside(element: HTMLElement, selector: string): HTMLElement {
  const found = element.closest<HTMLElement>(selector);
  if (!found) throw new Error(`no ${selector} around the element`);
  return found;
}

const panel = async () => within(await screen.findByRole("complementary", { name: "Chat" }));

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

describe("one kind of chat", () => {
  it("names the first Heinzel at first start and opens its chat", async () => {
    let sessions: object[] = [];
    const { calls } = mockOneChat([], {
      "/api/heinzel/sessions": () => ({ sessions }),
      "/api/heinzel/new-chat": async (request: Request) => {
        const body = await request.clone().json();
        names[helmi] = body.name;
        sessions = [summary(helmi)];
        return { session_id: helmi, heinzel_id: helmi, name: body.name, messages: [] };
      },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const chat = await panel();
    expect(await chat.findByRole("heading", { name: "Meet your first Heinzelmännchen" })).toBeInTheDocument();
    await expectAccessible(inside(chat.getByLabelText("What do you call it?"), "[data-pane], form"));
    await user.clear(chat.getByLabelText("What do you call it?"));
    await user.type(chat.getByLabelText("What do you call it?"), "Helmi");
    await user.click(chat.getByRole("button", { name: "Start" }));
    expect(await chat.findByRole("button", { name: "Helmi. Switch Heinzel" })).toBeInTheDocument();
    expect(chat.getByLabelText("Message Helmi")).toBeInTheDocument();
    expect(chat.queryByRole("button", { name: "Back to chat" })).not.toBeInTheDocument();
    const created = calls.filter((request) => new URL(request.url).pathname === "/api/heinzel/new-chat");
    expect(created).toHaveLength(1);
    expect(await created[0]?.clone().json()).toMatchObject({ name: "Helmi" });
    expect(localStorage.getItem("hz.chat.heinzel")).toBe(helmi);
    expect(localStorage.getItem("hz.chat.first-heinzel")).toBeNull();
  });

  it("keeps the first-start request after a lost answer, so a retry recovers the same Heinzel", async () => {
    const ids: string[] = [];
    mockOneChat([], {
      "/api/heinzel/new-chat": async (request: Request) => {
        ids.push((await request.clone().json()).request_id);
        return { status: 503, body: { detail: "acknowledgement lost" } };
      },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const chat = await panel();
    await user.clear(await chat.findByLabelText("What do you call it?"));
    await user.type(chat.getByLabelText("What do you call it?"), "Helmi");
    await user.click(chat.getByRole("button", { name: "Start" }));
    expect(await chat.findByRole("alert")).toHaveTextContent("Start again to recover the same one");
    await user.click(chat.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(ids).toHaveLength(2));
    expect(ids[1]).toBe(ids[0]);
  });

  it("opens your first Heinzel without a link, and the one you talked to last after that", async () => {
    mockOneChat([summary(fritz), summary(helmi)]);
    const first = renderAt("/doctor");
    expect(await (await panel()).findByRole("button", { name: "Helmi. Switch Heinzel" })).toBeInTheDocument();
    first.unmount();
    localStorage.setItem("hz.chat.heinzel", fritz);
    renderAt("/doctor");
    expect(await (await panel()).findByRole("button", { name: "Fritz. Switch Heinzel" })).toBeInTheDocument();
  });

  it("switches Heinzels from the name and offers New Heinzel, nothing else", async () => {
    mockOneChat([
      summary(fritz),
      summary(helmi),
      { ...summary("hz-01ARZ3NDEKTSV4RRFFQ69G5FAX"), name: "Old", state: "retired" },
    ]);
    const user = userEvent.setup();
    renderAt("/doctor");
    const chat = await panel();
    const trigger = await chat.findByRole("button", { name: "Helmi. Switch Heinzel" });
    await user.click(trigger);
    const list = within(await screen.findByRole("dialog", { name: "Your Heinzels" }));
    expect(list.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "FritzActive",
      "HelmiActive",
      "New Heinzel",
    ]);
    expect(list.getByRole("button", { name: /Helmi/ })).toHaveAttribute("aria-current", "true");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Your Heinzels" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    await user.click(trigger);
    await user.click(
      within(await screen.findByRole("dialog", { name: "Your Heinzels" })).getByRole("button", { name: /Fritz/ }),
    );
    expect(await chat.findByRole("button", { name: "Fritz. Switch Heinzel" })).toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem("hz.chat.heinzel")).toBe(fritz));
  });

  it("opens search and details from More, and Escape returns to More", async () => {
    mockOneChat([summary(helmi)]);
    const user = userEvent.setup();
    renderAt("/doctor");
    const chat = await panel();
    const more = await chat.findByRole("button", { name: "More: search, details" });
    // Details and search wait in More instead of standing among the messages.
    await waitFor(() => expect(chat.getByText("Details").closest("details")).not.toBeVisible());
    expect(chat.queryByRole("region", { name: "Search conversation" })).not.toBeInTheDocument();
    await user.click(more);
    // No earlier general chats: More holds search and details only.
    expect(
      within(await screen.findByRole("dialog", { name: "More" })).queryByRole("button", { name: "Earlier chats" }),
    ).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(more).toHaveFocus();
    await user.click(more);
    await user.click(
      within(await screen.findByRole("dialog", { name: "More" })).getByRole("button", { name: "Search" }),
    );
    expect(await chat.findByRole("region", { name: "Search conversation" })).toBeInTheDocument();
    await user.click(more);
    await user.click(
      within(await screen.findByRole("dialog", { name: "More" })).getByRole("button", { name: "Details" }),
    );
    expect(chat.getByText("Details").closest("details")).toBeVisible();
    await expectAccessible(inside(chat.getByRole("button", { name: "Helmi. Switch Heinzel" }), ".chat-panel-header"));
  });

  it("closes the chat from the header", async () => {
    mockOneChat([summary(helmi)]);
    const user = userEvent.setup();
    renderAt("/doctor");
    const chat = await panel();
    const header = inside(await chat.findByRole("button", { name: "Helmi. Switch Heinzel" }), ".chat-panel-header");
    await user.click(within(header).getByRole("button", { name: "Close the chat" }));
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "Chat" })).not.toBeInTheDocument());
  });

  it("opens the default Heinzel for an old general-chat context link, and opens no general chat", async () => {
    const { calls } = mockOneChat([summary(helmi)]);
    renderAt("/doctor?chat-context=event:ev42");
    expect(await (await panel()).findByRole("button", { name: "Helmi. Switch Heinzel" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Your message")).not.toBeInTheDocument();
    expect(calls.some((request) => new URL(request.url).pathname.includes("/runner/chat/open"))).toBe(false);
  });

  it("opens the default Heinzel for an old general chat's id, and never reads that chat", async () => {
    const { calls } = mockOneChat([summary(helmi)], { "/api/system/runner/chat/show": empty });
    renderAt(`/doctor?chat=${empty.id}`);
    expect(await (await panel()).findByRole("button", { name: "Helmi. Switch Heinzel" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Your message")).not.toBeInTheDocument();
    expect(calls.some((request) => new URL(request.url).pathname.endsWith("/runner/chat/show"))).toBe(false);
  });

  it("settles a first-start request once the list shows a Heinzel, after a lost answer and a reload", async () => {
    localStorage.setItem("hz.chat.first-heinzel", JSON.stringify({ id: "req-1", name: "Helmi" }));
    mockOneChat([summary(helmi)]);
    renderAt("/doctor");
    expect(await (await panel()).findByRole("button", { name: "Helmi. Switch Heinzel" })).toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem("hz.chat.first-heinzel")).toBeNull());
  });

  it("closes the switcher when Tab leaves it", async () => {
    mockOneChat([summary(helmi)]);
    const user = userEvent.setup();
    renderAt("/doctor");
    const chat = await panel();
    await user.click(await chat.findByRole("button", { name: "Helmi. Switch Heinzel" }));
    const list = await screen.findByRole("dialog", { name: "Your Heinzels" });
    await within(list).findByRole("button", { name: /Helmi/ });
    await user.tab();
    await user.tab();
    await user.tab();
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Your Heinzels" })).not.toBeInTheDocument());
  });

  it("asks for the list again when the switcher opens, so a Heinzel made elsewhere shows", async () => {
    let sessions = [summary(helmi)];
    mockOneChat([], { "/api/heinzel/sessions": () => ({ sessions }) });
    const user = userEvent.setup();
    renderAt("/doctor");
    const face = await (await panel()).findByRole("button", { name: "Helmi. Switch Heinzel" });
    sessions = [summary(fritz), summary(helmi)];
    await user.click(face);
    const list = within(await screen.findByRole("dialog", { name: "Your Heinzels" }));
    expect(await list.findByRole("button", { name: /Fritz/ })).toBeInTheDocument();
  });
});
