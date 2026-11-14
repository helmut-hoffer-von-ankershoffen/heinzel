import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { parseEvents } from "@/api/stream";
import { base, empty } from "@/test/chat";
import { mockApi, renderAt } from "@/test/render";

afterEach(() => sessionStorage.clear());

describe("ChatPanel", () => {
  it("clears the prior agent links when creating another Heinzel from the switcher", async () => {
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
          name: id === next ? "New personal Heinzel" : "Previous Heinzel",
          messages: [],
        };
      },
      "/api/heinzel/new-chat": { session_id: next, heinzel_id: next, name: "New personal Heinzel", messages: [] },
      "/api/heinzel/sessions": { sessions: [] },
      "/api/system/runner/chat/show": oldChat,
    });
    const user = userEvent.setup();
    renderAt(
      `/doctor?chat=${previous}&agent-chat=${oldChat.id}&approval-chat=${oldChat.id}&approval-request=old&agent-open-request=old`,
    );
    await user.click(await screen.findByRole("button", { name: "Previous Heinzel. Switch Heinzel" }));
    await user.click(await screen.findByRole("button", { name: "New Heinzel" }));
    // The page's draft asks for the name first; saving it creates the Heinzel and opens its conversation.
    await user.type(await within(await screen.findByRole("main")).findByLabelText("Name"), "New personal Heinzel");
    await user.click(screen.getByRole("button", { name: "Save and chat" }));
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByRole("button", { name: "New personal Heinzel. Switch Heinzel" })).toBeInTheDocument();
    await user.type(await panel.findByLabelText("Message New personal Heinzel"), "Keep this in the new conversation");
    expect(panel.getByRole("button", { name: "Send" })).toBeEnabled();
    const created = calls.findIndex((request) => new URL(request.url).pathname.endsWith("/heinzel/new-chat"));
    expect(
      calls.slice(created + 1).some((request) => new URL(request.url).pathname.endsWith("/runner/chat/show")),
    ).toBe(false);
  });
});

describe("parseEvents", () => {
  it("returns complete events and keeps a partial one for the next chunk", () => {
    const { events, rest } = parseEvents('event: text\ndata: {"text":"Hi"}\n\nevent: result\ndata: {"da');
    expect(events).toEqual([{ event: "text", data: { text: "Hi" } }]);
    expect(rest).toBe('event: result\ndata: {"da');
  });
});
