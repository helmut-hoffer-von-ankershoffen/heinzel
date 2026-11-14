// One kind of chat (flag `one_chat`, bead agqz): every chat is with a Heinzel, there is no "Back to chat", and the
// header carries the face, the name with its switcher, More and Close.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { base } from "@/test/chat";
import { config } from "@/test/fixtures";
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
const flagged = (features: string[]) => ({
  ...config,
  settings: [
    ...config.settings,
    { section: "hz", key: "features", value: features, description: "", env: "HZ_FEATURES" },
  ],
});

function mockOneChat(sessions: object[], extra: Record<string, unknown> = {}) {
  return mockApi({
    ...base,
    "/api/system/config/show": flagged(["one_chat"]),
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
    await expectAccessible(inside(chat.getByRole("heading", { name: "Meet your first Heinzelmännchen" }), "form"));
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
    await user.type(await chat.findByLabelText("What do you call it?"), "Helmi");
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
    const more = await chat.findByRole("button", { name: "More: search, details, earlier chats" });
    // Details and search wait in More instead of standing among the messages.
    await waitFor(() => expect(chat.getByText("Details").closest("details")).not.toBeVisible());
    expect(chat.queryByRole("region", { name: "Search conversation" })).not.toBeInTheDocument();
    await user.click(more);
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

  it("keeps the earlier general chats one step away, with the way back to the Heinzel", async () => {
    mockOneChat([summary(helmi)], { "/api/system/runner/chat/show": { status: 404, body: {} } });
    const user = userEvent.setup();
    renderAt("/doctor");
    const chat = await panel();
    await user.click(await chat.findByRole("button", { name: "More: search, details, earlier chats" }));
    await user.click(
      within(await screen.findByRole("dialog", { name: "More" })).getByRole("button", { name: "Earlier chats" }),
    );
    expect(await chat.findByLabelText("Your message")).toBeInTheDocument();
    await user.click(chat.getByRole("button", { name: "Chat with Helmi" }));
    expect(await chat.findByRole("button", { name: "Helmi. Switch Heinzel" })).toBeInTheDocument();
  });

  it("closes the chat from the header", async () => {
    mockOneChat([summary(helmi)]);
    const user = userEvent.setup();
    renderAt("/doctor");
    const chat = await panel();
    await user.click(await chat.findByRole("button", { name: "Close the chat" }));
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "Chat" })).not.toBeInTheDocument());
  });

  it("leaves today's chat unchanged with the flag off", async () => {
    mockApi({ ...base, "/api/heinzel/sessions": { sessions: [summary(helmi)] }, "/api/heinzel/session": snapshot });
    renderAt(`/doctor?chat=${helmi}`);
    const chat = await panel();
    expect(await chat.findByRole("button", { name: "Back to chat" })).toBeInTheDocument();
    expect(chat.queryByRole("button", { name: "Helmi. Switch Heinzel" })).not.toBeInTheDocument();
  });
});
