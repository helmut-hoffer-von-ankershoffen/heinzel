import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { doctorCards, eventCards, jobCards, type TodayCard } from "@/chat/today";
import { assertActionStructure } from "@/components/PaneActions";
import { base } from "@/test/chat";
import { doctor } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

const [doctorCard] = doctorCards(doctor) as [TodayCard];

describe("Home", () => {
  it("has Today on top and one chat beside it, with a Heinzel: the first start while there is none", async () => {
    mockApi(base);
    const { container } = renderAt("/");
    expect(await screen.findByRole("heading", { name: "Today" })).toBeInTheDocument();
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await chat.findByRole("heading", { name: "Meet your first Heinzelmännchen" })).toBeInTheDocument();
    // No general chat: no composer without a Heinzel, and no second chat.
    expect(screen.queryByLabelText("Your message")).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Ask" })).not.toBeInTheDocument();
    await screen.findByRole("heading", { name: "Doctor: 2 checks need attention" });
    await expectAccessible(container);
  });

  it("keeps Today's heading for screen readers only, since the header already says Today", async () => {
    mockApi(base);
    renderAt("/");
    const heading = await screen.findByRole("heading", { level: 1, name: "Today" });
    expect(heading).toHaveClass("sr-only");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("region", { name: "Today" })).toBeInTheDocument();
  });

  it("gives the main region no focus ring of its own, leaving controls their ring", async () => {
    mockApi(base);
    renderAt("/");
    const main = await screen.findByRole("main");
    expect(main).toHaveAttribute("data-focus-ring", "parent");
    expect(main).toHaveClass("outline-none");
    expect(await screen.findByRole("link", { name: "Open" })).not.toHaveAttribute("data-focus-ring");
  });

  it("asks the default Heinzel about a card: Ask <Name> types the question into its composer, never sends it", async () => {
    const helmi = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
    const { calls } = mockApi({
      ...base,
      "/api/heinzel/sessions": {
        sessions: [
          { session_id: helmi, name: "Helmi", state: "awake", last_seq: 1, updated_at: "2026-11-14T09:00:00Z" },
        ],
      },
      "/api/heinzel/session": { session_id: helmi, heinzel_id: helmi, name: "Helmi", messages: [] },
    });
    const user = userEvent.setup();
    renderAt("/");
    const card = (await screen.findByRole("heading", { name: "Doctor: 2 checks need attention" })).closest(
      "li",
    ) as HTMLElement;
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    const composer = (await chat.findByLabelText("Message Helmi")) as HTMLTextAreaElement;
    await user.type(composer, "Earlier words");
    await user.click(await within(card).findByRole("button", { name: "Ask Helmi" }));
    // The chat now links the default Heinzel (`?chat=`), so its composer is shown afresh: read it again.
    const asked = () => chat.getByLabelText("Message Helmi") as HTMLTextAreaElement;
    await waitFor(() => expect(asked().value).toMatch(/^Earlier words\n\nDoctor: 2 checks need attention/));
    await waitFor(() => expect(asked()).toHaveFocus());
    const saved = JSON.parse(sessionStorage.getItem(`hz.durable.composer.${helmi}`) ?? "{}");
    expect(saved.text).toBe(asked().value);
    // Typed, not sent: no message goes to the Heinzel.
    expect(calls.some((request) => request.method === "POST" && /\/heinzel\/(chat|message)/.test(request.url))).toBe(
      false,
    );
    assertActionStructure(card);
  });

  it("offers no Ask while no Heinzel is named yet", async () => {
    mockApi(base);
    renderAt("/");
    const card = (await screen.findByRole("heading", { name: "Doctor: 2 checks need attention" })).closest(
      "li",
    ) as HTMLElement;
    expect(within(card).queryByRole("button", { name: /^Ask/ })).not.toBeInTheDocument();
  });

  it("leads with Open and dismisses from the corner; there is no general chat to ask in", async () => {
    mockApi(base);
    renderAt("/");
    const card = (await screen.findByRole("heading", { name: "Doctor: 2 checks need attention" })).closest(
      "li",
    ) as HTMLElement;
    expect(within(card).queryByRole("button", { name: "Ask about it" })).not.toBeInTheDocument();
    const open = within(card).getByRole("link", { name: "Open" });
    expect(open).toHaveTextContent("Open");
    const dismiss = within(card).getByRole("button", { name: /^Dismiss/ });
    expect(dismiss.closest("[data-pane-chrome]")).not.toBeNull();
    expect(dismiss).toHaveTextContent("");
    assertActionStructure(card);
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

  it("teases the unread notifications: a count, the latest few and a link to all of them", async () => {
    const notice = (n: number) => ({
      id: `20261025T10000${n}000000Z-abc${n}`,
      kind: "failed",
      title: `Run ${n} failed`,
      body: "",
      route: `/runs/${n}`,
      command: "hz system doctor",
      key: `failed:${n}`,
      urgent: false,
      at: "2026-10-25T10:00:00Z",
      due: "2026-10-25T10:00:00Z",
      popup: true,
      held: null,
      delivered_by: null,
      delivered_at: null,
    });
    mockApi({ ...base, "/api/system/notify/list": { notices: [1, 2, 3, 4].map(notice), waiting: 0, native: "" } });
    const { container } = renderAt("/");
    const card = (await screen.findByRole("heading", { name: /^Notifications.*unread$/ })).closest("li") as HTMLElement;
    expect(within(card).getByText("4 unread")).toBeInTheDocument();
    expect(within(card).getAllByRole("link", { name: /^Run \d failed/ })).toHaveLength(3);
    const all = within(card).getByRole("link", { name: "All notifications" });
    expect(all).toHaveAttribute("href", "/?panel=notifications");
    expect(all).toHaveTextContent("All notifications");
    assertActionStructure(card);
    await expectAccessible(container);
  });

  it("teases a repeated notice once", async () => {
    const notice = (n: number) => ({
      id: `20261025T10000${n}000000Z-abc${n}`,
      kind: "test",
      title: "Notifications work",
      body: "",
      route: "/",
      command: "hz system notify test",
      key: `test:${n}`,
      urgent: false,
      at: "2026-10-25T10:00:00Z",
      due: "2026-10-25T10:00:00Z",
      popup: true,
      held: null,
      delivered_by: null,
      delivered_at: null,
    });
    mockApi({ ...base, "/api/system/notify/list": { notices: [1, 2, 3].map(notice), waiting: 0, native: "" } });
    renderAt("/");
    const card = (await screen.findByRole("heading", { name: /^Notifications.*unread$/ })).closest("li") as HTMLElement;
    expect(within(card).getByText("3 unread")).toBeInTheDocument();
    expect(within(card).getAllByText("Notifications work")).toHaveLength(1);
  });

  it("shows no notifications card when nothing is unread", async () => {
    mockApi({ ...base, "/api/system/notify/list": { notices: [], waiting: 0, native: "" } });
    renderAt("/");
    await screen.findByRole("heading", { name: "Today" });
    expect(screen.queryByRole("heading", { name: /^Notifications.*unread$/ })).not.toBeInTheDocument();
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
    command: "hz heinzel list",
    key: "weekly",
    urgent: false,
    at: new Date().toISOString(),
    due: new Date().toISOString(),
    popup: true,
  };
  const list = { notices: [notice], waiting: 0, native: "macOS notifications" };

  it("shows the day's notices as cards that open their screen", async () => {
    mockApi({ ...base, "/api/system/notify/list": list });
    renderAt(`/?card=${encodeURIComponent(`event:${notice.id}`)}`);
    const card = (await screen.findByRole("heading", { name: notice.title })).closest("li") as HTMLElement;
    expect(card).toHaveAttribute("aria-current", "true");
    expect(within(card).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/contracts");
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
