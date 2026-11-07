import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { expectPane } from "@/test/pane";
import { mockApi, renderAt } from "@/test/render";

const sessions = [
  { session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV", name: "Morning check", state: "awake", last_seq: 4 },
  { session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW", name: "Quiet helper", state: "paused", last_seq: 2 },
  { session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAX", name: "Waiting helper", state: "waiting", last_seq: 3 },
  { session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAY", name: "New helper", state: "proposed", last_seq: 0 },
];

describe("The durable Heinzel footer control", () => {
  it("counts persisted sessions and reports only lifecycle states returned by the service", async () => {
    mockApi({ ...shell, "/api/heinzel/sessions": { sessions } });
    renderAt("/doctor");
    const button = await screen.findByRole("button", { name: "4 Heinzelmännchen, off" });
    fireEvent.click(button);
    const panel = await screen.findByRole("region", { name: "Heinzelmännchen" });
    expect(within(panel).getByText("4 persisted personal Heinzels")).toBeInTheDocument();
    expect(within(panel).getByText("Active").nextElementSibling).toHaveTextContent("1");
    expect(within(panel).getByText("Paused").nextElementSibling).toHaveTextContent("1");
    expect(within(panel).getByText("Needs attention").nextElementSibling).toHaveTextContent("1");
    expect(within(panel).getByText("Proposed").nextElementSibling).toHaveTextContent("1");
    // Content first, then the divider and the one bottom row of buttons with icon and text (design/README.md).
    await expectPane(panel);
    const row = within(panel).getByRole("group", { name: "Actions" });
    const chat = within(row).getByRole("link", { name: "Open chat" });
    expect(chat).toHaveAttribute("href", "/");
    expect(chat).toHaveTextContent("Open chat");
    expect(chat.querySelector("svg")).not.toBeNull();
  });

  it("shows zero for an authoritative empty persisted list", async () => {
    mockApi({ ...shell, "/api/heinzel/sessions": { sessions: [] } });
    renderAt("/doctor");
    expect(await screen.findByRole("button", { name: "0 Heinzelmännchen, off" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "0 Heinzelmännchen, off" }));
    expect(await screen.findByText("No persisted Heinzels yet.")).toBeInTheDocument();
    // The empty state's action is the pane's primary button in the bottom row under the divider.
    const panel = screen.getByRole("region", { name: "Heinzelmännchen" });
    await expectPane(panel);
    // Nothing between the content and the divider: both actions are buttons in the bottom row, Create first.
    const row = within(panel).getByRole("group", { name: "Actions" });
    expect([...row.querySelectorAll("a, button")].map((action) => action.textContent)).toEqual([
      "Create a personal Heinzel",
      "Open chat",
    ]);
    expect(within(row).getByRole("button", { name: "Create a personal Heinzel" })).not.toHaveClass("underline");
  });

  it("creates a durable personal Heinzel and opens its inspectable session", async () => {
    const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    const created = {
      session_id: sessionId,
      heinzel_id: sessionId,
      name: "Personal Heinzel",
      schedule: null,
      messages: [],
    };
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions: [] },
      "/api/heinzel/new-chat": (request: Request) => {
        expect(request.method).toBe("POST");
        return created;
      },
      "/api/heinzel/session": created,
    });
    renderAt("/doctor");
    fireEvent.click(await screen.findByRole("button", { name: "0 Heinzelmännchen, off" }));
    fireEvent.click(await screen.findByRole("button", { name: "Create a personal Heinzel" }));
    expect(await screen.findByRole("heading", { name: "Personal Heinzel" })).toBeInTheDocument();
    const request = calls.find((item) => new URL(item.url).pathname.endsWith("/new-chat"));
    expect(request).toBeDefined();
    if (!request) throw new Error("The personal Heinzel creation request was not sent");
    expect(await request.json()).toMatchObject({ name: "Personal Heinzel" });
  });

  it("shows a loading placeholder without implying a count before the persisted list arrives", async () => {
    let finish: (response: Response) => void = () => {};
    mockApi({
      ...shell,
      "/api/system/info": { version: "0.7.12", modules: [] },
      "/api/system/ui/status": [],
      "/api/heinzel/sessions": { sessions: [] },
    });
    const normalFetch = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: Request) => {
        if (new URL(input.url).pathname === "/api/heinzel/sessions")
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        return normalFetch(input);
      }),
    );
    renderAt("/doctor");
    const button = await screen.findByRole("button", { name: "… Heinzelmännchen, off" });
    fireEvent.click(button);
    expect(await screen.findByText("Loading…")).toHaveAttribute("role", "status");
    finish(Response.json({ sessions: [] }));
    expect(await screen.findByRole("button", { name: "0 Heinzelmännchen, off" })).toBeInTheDocument();
  });

  it("does not reuse or invent a count when the persisted list cannot be read", async () => {
    mockApi({ ...shell, "/api/heinzel/sessions": { status: 503, body: { error: "unavailable" } } });
    renderAt("/doctor");
    const button = await screen.findByRole("button", { name: "? Heinzelmännchen, error" });
    fireEvent.click(button);
    const panel = await screen.findByRole("region", { name: "Heinzelmännchen" });
    expect(within(panel).getByRole("status")).toHaveTextContent("System health could not be refreshed.");
    expect(within(panel).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(within(panel).queryByText(/persisted personal Heinzels/)).not.toBeInTheDocument();
  });
});
