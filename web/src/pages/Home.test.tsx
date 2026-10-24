import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { doctorCards, eventCards, jobCards, type TodayCard } from "@/chat/today";
import { answered, base, empty, frame, mockWithStream, msg } from "@/test/chat";
import { doctor } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

const [doctorCard] = doctorCards(doctor) as [TodayCard];

describe("Home", () => {
  it("is the chat: Today on top, the composer focused, and no second chat on the right", async () => {
    mockApi(base);
    const { container } = renderAt("/");
    expect(await screen.findByRole("heading", { name: "Today" })).toBeInTheDocument();
    expect(screen.getByLabelText("Your message")).toHaveFocus();
    expect(screen.queryByRole("complementary", { name: "Ask" })).not.toBeInTheDocument();
    expect(await screen.findByText(/at most \$0\.25 per message/)).toBeInTheDocument();
    await screen.findByRole("heading", { name: "Doctor: 2 checks need attention" });
    await expectAccessible(container);
  });

  it("shows what needs you as cards that open their screen", async () => {
    mockApi(base);
    renderAt("/");
    const card = (await screen.findByRole("heading", { name: "Doctor: 2 checks need attention" })).closest(
      "li",
    ) as HTMLElement;
    expect(within(card).getByText("system.doctor uv, org profile")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/doctor");
  });

  it("opens a chat about a card with what it says, and keeps the conversation below", async () => {
    const about = { ...empty, context: { kind: "route" as const, ref: "/doctor", detail: "" } };
    const { calls } = mockApi({ ...base, "/api/system/runner/chat/open": about });
    const user = userEvent.setup();
    renderAt("/");
    const card = (await screen.findByRole("heading", { name: doctorCard.title })).closest("li") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "Ask about it" }));
    expect(await screen.findByText("About: route /doctor")).toBeInTheDocument();
    const open = calls.find((request) => request.url.includes("/chat/open"));
    const query = new URL(open?.url ?? "").searchParams;
    expect(query.get("context")).toBe("route:/doctor");
    expect(query.get("detail")).toBe(`${doctorCard.title}: ${doctorCard.detail}`);
    expect(screen.getByLabelText("Your message")).toHaveFocus();
  });

  it("dismisses a card until what it says changes", async () => {
    mockApi(base);
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await screen.findByRole("button", { name: `Dismiss ${doctorCard.title}` }));
    expect(screen.queryByRole("heading", { name: doctorCard.title })).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("hz.today.dismissed") ?? "[]")).toEqual([doctorCard.id]);
    expect(doctorCards({ ...doctor, status: "down" })[0]?.id).not.toBe(doctorCard.id);
  });

  it("focuses the card a notification links to", async () => {
    mockApi(base);
    renderAt(`/?card=${encodeURIComponent(doctorCard.id)}`);
    const card = (await screen.findByRole("heading", { name: doctorCard.title })).closest("li") as HTMLElement;
    expect(card).toHaveAttribute("aria-current", "true");
  });

  it("streams the answer with the tools it calls, then folds them under the answer", async () => {
    const done = {
      ...answered,
      actions: [],
      messages: [
        msg({ role: "user", text: "How healthy is hz?" }),
        msg({ role: "assistant", text: "Two checks are degraded.", tools: { mcp__hz__system_doctor: 1 } }),
      ],
    };
    mockWithStream({ ...base, "/api/system/runner/chat/open": empty }, [
      frame("tool", { name: "mcp__hz__system_doctor" }),
      frame("result", { data: done }),
    ]);
    const user = userEvent.setup();
    renderAt("/");
    await user.type(screen.getByLabelText("Your message"), "How healthy is hz?{Enter}");
    expect(await screen.findByText("Two checks are degraded.")).toBeInTheDocument();
    expect(screen.getByText("Used 1 tools")).toBeInTheDocument();
    expect(screen.getByText("system_doctor")).toBeInTheDocument();
  });

  it("is quiet when nothing needs you", async () => {
    mockApi({ ...base, "/api/system/doctor": { status: "up", checks: {} } });
    renderAt("/");
    expect(await screen.findByText(/Nothing needs you right now/)).toBeInTheDocument();
  });
});

describe("Today's notices", () => {
  const notice = {
    id: "20261024T0900-ab12",
    kind: "failed" as const,
    title: "The weekly digest failed",
    body: "The tracker refused the token.",
    route: "/contracts",
    command: "hz workflow list",
    key: "weekly",
    urgent: false,
    at: new Date().toISOString(),
    due: new Date().toISOString(),
    popup: true,
  };
  const list = { notices: [notice], waiting: 0, native: "macOS notifications" };

  it("shows the day's notices as cards, and a chat about one gets its event as context", async () => {
    const about = { ...empty, context: { kind: "event" as const, ref: notice.id, detail: "" } };
    const { calls } = mockApi({
      ...base,
      "/api/system/notify/list": list,
      "/api/system/runner/chat/open": about,
    });
    const user = userEvent.setup();
    renderAt(`/?card=${encodeURIComponent(`event:${notice.id}`)}`);
    const card = (await screen.findByRole("heading", { name: notice.title })).closest("li") as HTMLElement;
    expect(card).toHaveAttribute("aria-current", "true");
    expect(within(card).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/contracts");
    await user.click(within(card).getByRole("button", { name: "Ask about it" }));
    const open = calls.find((request) => request.url.includes("/chat/open"));
    expect(new URL(open?.url ?? "").searchParams.get("context")).toBe(`event:${notice.id}`);
  });

  it("leaves out notices older than a day", () => {
    const old = { ...notice, at: new Date(Date.now() - 2 * 86_400_000).toISOString() };
    expect(eventCards({ ...list, notices: [old] })).toEqual([]);
  });
});

describe("Today's cards", () => {
  it("names a failed run with its problem", () => {
    const [card] = jobCards([
      {
        job: "workflow:me-weekly",
        catch_up: "skip",
        last_as_of: null,
        last_disposition: "failed",
        next_run: null,
        problem: "the tracker refused",
        retries: 0,
        schedule: "0 9 * * 1",
        scope: "user",
      } as never,
    ]);
    expect(card?.title).toBe("A run failed: workflow:me-weekly");
    expect(card?.detail).toBe("the tracker refused");
  });
});
