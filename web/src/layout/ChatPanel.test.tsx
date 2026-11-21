import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { parseEvents } from "@/api/stream";
import { base, empty } from "@/test/chat";
import { mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";

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
    await user.click(await screen.findByRole("button", { name: /^Previous Heinzel, .+\. Switch Heinzel$/ }));
    await user.click(await screen.findByRole("button", { name: "New Heinzel" }));
    // The page's draft asks for the name first; saving it creates the Heinzel and opens its conversation.
    await user.type(await within(await screen.findByRole("main")).findByLabelText("Name"), "New personal Heinzel");
    await user.click(screen.getByRole("button", { name: "Save and chat" }));
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(
      await panel.findByRole("button", { name: /^New personal Heinzel, .+\. Switch Heinzel$/ }),
    ).toBeInTheDocument();
    await user.type(await panel.findByLabelText("Message New personal Heinzel"), "Keep this in the new conversation");
    expect(panel.getByRole("button", { name: "Send" })).toBeEnabled();
    const created = calls.findIndex((request) => new URL(request.url).pathname.endsWith("/heinzel/new-chat"));
    expect(
      calls.slice(created + 1).some((request) => new URL(request.url).pathname.endsWith("/runner/chat/show")),
    ).toBe(false);
  });

  it("keeps the new Heinzel's chat, and what is typed in it, when Start links it", async () => {
    const helmi = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    let created = false;
    mockApi({
      ...base,
      "/api/heinzel/new-chat": () => {
        created = true;
        return { session_id: helmi, heinzel_id: helmi, name: "Helmi", messages: [] };
      },
      "/api/heinzel/sessions": () => ({
        sessions: created
          ? [{ session_id: helmi, name: "Helmi", state: "awake", last_seq: 0, updated_at: "2026-11-14T09:00:00Z" }]
          : [],
      }),
      "/api/heinzel/session": { session_id: helmi, heinzel_id: helmi, name: "Helmi", messages: [] },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    await user.click(await panel.findByRole("button", { name: "Start" }));
    const composer = await panel.findByLabelText("Message Helmi");
    await waitFor(() => expect(composer).toBeEnabled());
    await user.type(composer, "Hello right after Start");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    // One chat instance from Start on: the composer the person typed in is still the one shown, with their words.
    expect(panel.getByLabelText("Message Helmi")).toHaveValue("Hello right after Start");
    expect(panel.getByLabelText("Message Helmi")).toBe(composer);
  });
});

describe("parseEvents", () => {
  it("returns complete events and keeps a partial one for the next chunk", () => {
    const { events, rest } = parseEvents('event: text\ndata: {"text":"Hi"}\n\nevent: result\ndata: {"da');
    expect(events).toEqual([{ event: "text", data: { text: "Hi" } }]);
    expect(rest).toBe('event: result\ndata: {"da');
  });
});
