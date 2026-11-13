import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { expectPane } from "@/test/pane";
import { mockApi, renderAt } from "@/test/render";

const sessions = [
  {
    session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV",
    name: "Morning check",
    state: "awake",
    last_seq: 4,
    appearance: { age: "old", build: "round", hat: "blue" },
  },
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
    expect(within(panel).getByText("Active", { selector: "dt" }).nextElementSibling).toHaveTextContent("1");
    expect(within(panel).getByText("Paused", { selector: "dt" }).nextElementSibling).toHaveTextContent("1");
    expect(within(panel).getByText("Needs attention", { selector: "dt" }).nextElementSibling).toHaveTextContent("1");
    expect(within(panel).getByText("Proposed", { selector: "dt" }).nextElementSibling).toHaveTextContent("1");
    // Each Heinzel is listed with its own look and its state in words.
    const rows = within(within(panel).getByRole("list", { name: "Your Heinzels" })).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("Morning check");
    expect(rows[0]).toHaveTextContent("Active");
    expect(rows[0]?.querySelector('svg[data-view="bust"] [fill="#1E88E5"]')).not.toBeNull();
    expect(rows[3]).toHaveTextContent("New");
    expect(button.querySelectorAll('svg[data-view="bust"]')).toHaveLength(3);
    // Content first, then the divider and the one bottom row of buttons with icon and text (design/README.md).
    await expectPane(panel);
    const row = within(panel).getByRole("group", { name: "Actions" });
    const chat = within(row).getByRole("link", { name: "Open chat" });
    expect(chat).toHaveAttribute("href", "/");
    // The only action leads with its words (design/README.md, "Actions").
    expect(chat).toHaveTextContent("Open chat");
    expect(chat.querySelector("svg")).not.toBeNull();
  });

  it("leaves retired Heinzels out of the count and the faces, and counts them on their own line", async () => {
    const retired = {
      ...sessions[0],
      session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAZ",
      name: "Old helper",
      state: "retired",
    };
    mockApi({ ...shell, "/api/heinzel/sessions": { sessions: [retired, ...sessions] } });
    renderAt("/doctor");
    fireEvent.click(await screen.findByRole("button", { name: "4 Heinzelmännchen, off" }));
    const panel = await screen.findByRole("region", { name: "Heinzelmännchen" });
    expect(within(panel).queryByText("Old helper")).toBeNull();
    expect(within(panel).getByText("Retired", { selector: "dt" }).nextElementSibling).toHaveTextContent("1");
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
    // One row: Create leads with its words, Open chat is an icon named by its words.
    const row = within(panel).getByRole("group", { name: "Actions" });
    const [create, chat] = [...row.querySelectorAll("a, button")];
    expect(create).toHaveTextContent("Create a personal Heinzel");
    expect(chat).toHaveAttribute("aria-label", "Open chat");
    expect(chat).toHaveAttribute("title", "Open chat");
    expect(chat).toHaveTextContent(/^$/);
    expect(within(row).getByRole("link", { name: "Create a personal Heinzel" })).not.toHaveClass("underline");
  });

  it("opens the draft of a new Heinzel without creating one", async () => {
    const { calls } = mockApi({ ...shell, "/api/heinzel/sessions": { sessions: [] } });
    renderAt("/doctor");
    fireEvent.click(await screen.findByRole("button", { name: "0 Heinzelmännchen, off" }));
    const panel = await screen.findByRole("region", { name: "Heinzelmännchen" });
    fireEvent.click(within(panel).getByRole("link", { name: "Create a personal Heinzel" }));
    // The page asks for a name and a look first; nothing exists until that draft is saved.
    expect(await screen.findByRole("heading", { name: "Your new Heinzel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save and chat" })).toBeDisabled();
    expect(calls.some((item) => new URL(item.url).pathname.endsWith("/new-chat"))).toBe(false);
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
