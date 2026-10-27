import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const snapshot = {
  session_id: sessionId,
  name: "Personal helper",
  messages: [{ id: "m1", seq: 1, role: "user", text: "Hello", status: "pending" }],
};

describe("personal conversation links", () => {
  it("opens the owned durable session in the chat panel even when it was closed", async () => {
    localStorage.setItem("hz.chat.open", "false");
    const { calls } = mockApi({ ...shell, "/api/heinzel/session": snapshot });
    const { container } = renderAt(`/heinzel?chat=${sessionId}`);
    const panel = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await panel.findByRole("heading", { name: "Personal helper" })).toBeInTheDocument();
    expect(panel.getByText("Hello")).toBeInTheDocument();
    expect(panel.getByText(/Messages are queued/)).toBeInTheDocument();
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
});
