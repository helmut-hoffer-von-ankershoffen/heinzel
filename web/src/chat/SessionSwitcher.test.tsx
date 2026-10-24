import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { answered, at, base, empty, frame, mockWithHeldStream } from "@/test/chat";
import { heinzelmaennchen } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { ago, heinzelOf, NEW_HEINZEL, switcherGroups } from "./SessionSwitcher";

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

const contract = (ref: string) => ({ kind: "contract" as const, ref, detail: "" });
const weekly = {
  id: "cccccccccccccccc",
  title: "contract me:weekly-digest",
  messages: 3,
  pending: 0,
  spent_usd: 0.02,
  updated: at,
  context: contract("me:weekly-digest"),
};
const sessions = [
  weekly,
  { id: "aaaaaaaaaaaaaaaa", title: "Make me the lead", messages: 2, pending: 1, spent_usd: 0.0123, updated: at },
  { id: "bbbbbbbbbbbbbbbb", title: "Why is doctor red?", messages: 4, pending: 0, spent_usd: 0.05, updated: at },
];
const all = heinzelmaennchen.heinzelmaennchen;
const opened = (calls: Request[]) =>
  calls.filter((call) => call.url.includes("/chat/open")).map((call) => new URL(call.url).searchParams.get("context"));

describe("The chat's switcher of Heinzelmännchen", () => {
  it("lists your Heinzelmännchen, Ask hz and earlier questions, searchable, and continues the one chosen", async () => {
    const { calls } = mockApi({
      ...base,
      "/api/system/runner/chat/list": sessions,
      "/api/system/runner/chat/show": answered,
    });
    const user = userEvent.setup();
    const { container } = renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "Ask hz: switch" }));
    const list = screen.getByRole("dialog", { name: "Your Heinzelmännchen and questions" });
    const mine = await within(list).findByRole("region", { name: "Your Heinzelmännchen" });
    expect(within(mine).getByRole("button", { name: /Weekly digest/ })).toHaveTextContent("scheduled");
    expect(within(mine).getByRole("button", { name: /Team report/ })).toHaveTextContent("no conversation yet");
    expect(within(list).getByRole("region", { name: "Questions" })).toHaveTextContent("Ask hz");
    const earlier = within(list).getByRole("region", { name: "Earlier questions" });
    expect(within(earlier).getByRole("button", { name: /Why is doctor red\?/ })).toHaveTextContent("4 messages");
    // The Heinzelmännchen's own conversation isn't an earlier question too.
    expect(within(earlier).queryByText("contract me:weekly-digest")).not.toBeInTheDocument();
    await expectAccessible(container);
    await user.type(within(list).getByRole("searchbox", { name: "Search Heinzelmännchen" }), "weekly");
    expect(within(list).queryByText("Team report")).not.toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: /Weekly digest/ }));
    await waitFor(() => expect(sessionStorage.getItem("hz.chat")).toBe("cccccccccccccccc"));
    expect(opened(calls)).toEqual([]); // its conversation continues; nothing new is opened
  });

  it("opens the conversation of a Heinzelmännchen that has none yet, about its contract", async () => {
    const { calls } = mockApi({
      ...base,
      "/api/system/runner/chat/list": sessions,
      "/api/system/runner/chat/open": { ...empty, context: contract("team:team-report"), title: "contract" },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "Ask hz: switch" }));
    const list = screen.getByRole("dialog", { name: "Your Heinzelmännchen and questions" });
    await user.click(await within(list).findByRole("button", { name: /Team report/ }));
    await waitFor(() => expect(opened(calls)).toEqual(["contract:team:team-report"]));
    expect(await screen.findByRole("button", { name: "Team report: switch" })).toBeInTheDocument();
  });

  it("opens Ask hz as a chat about nothing in particular", async () => {
    const { calls } = mockApi({
      ...base,
      "/api/system/runner/chat/list": sessions,
      "/api/system/runner/chat/open": { ...empty, context: null, title: "Ask hz" },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "Ask hz: switch" }));
    const list = screen.getByRole("dialog", { name: "Your Heinzelmännchen and questions" });
    await user.click(within(list).getByRole("button", { name: /^Ask hz/ }));
    await waitFor(() => expect(opened(calls)).toEqual([null]));
  });

  it("starts a new Heinzelmännchen as a conversation, once until it has begun", async () => {
    const unstarted = { kind: "text" as const, ref: NEW_HEINZEL.slice(5), detail: "" };
    const { calls } = mockApi({
      ...base,
      "/api/system/runner/chat/list": sessions,
      "/api/system/runner/chat/open": { ...empty, context: unstarted },
      "/api/system/runner/chat/show": { ...empty, context: unstarted },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "New Heinzelmännchen" }));
    await waitFor(() => expect(opened(calls)).toEqual([NEW_HEINZEL]));
    await waitFor(() => expect(screen.getByLabelText("Your message")).toHaveFocus());
    // Not begun yet: the header's + and the list's New put you back in it instead of opening another.
    await user.click(screen.getByRole("button", { name: "New Heinzelmännchen" }));
    await user.click(screen.getByRole("button", { name: /: switch$/ }));
    const list = screen.getByRole("dialog", { name: "Your Heinzelmännchen and questions" });
    await user.click(within(list).getByRole("button", { name: /^New Heinzelmännchen/ }));
    expect(opened(calls)).toEqual([NEW_HEINZEL]);
    expect(screen.getByLabelText("Your message")).toHaveFocus();
  });

  it("stays in a Heinzelmännchen's conversation when it is chosen again", async () => {
    sessionStorage.setItem("hz.chat", weekly.id);
    const { calls } = mockApi({
      ...base,
      "/api/system/runner/chat/list": [],
      "/api/system/runner/chat/show": { ...answered, id: weekly.id, context: weekly.context },
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "Weekly digest: switch" }));
    const list = screen.getByRole("dialog", { name: "Your Heinzelmännchen and questions" });
    const row = await within(list).findByRole("button", { name: /Weekly digest/ });
    expect(row).toHaveAttribute("aria-current", "true");
    await user.click(row);
    expect(opened(calls)).toEqual([]);
    expect(sessionStorage.getItem("hz.chat")).toBe(weekly.id);
  });

  it("names any other chat by its first message", async () => {
    sessionStorage.setItem("hz.chat", answered.id);
    mockApi({ ...base, "/api/system/runner/chat/show": { ...answered, title: "Make me the lead" } });
    renderAt("/doctor");
    expect(await screen.findByRole("button", { name: "Make me the lead: switch" })).toBeInTheDocument();
  });

  it("moves to a new Heinzelmännchen while an answer is written, leaving that turn to finish in its own session", async () => {
    sessionStorage.setItem("hz.chat", answered.id);
    const { release } = mockWithHeldStream(
      {
        ...base,
        "/api/system/runner/chat/show": answered,
        "/api/system/runner/chat/open": { ...empty, id: "fedcba9876543210" },
      },
      [frame("text", { text: "Thinking it through" })],
      [frame("result", { data: answered })],
    );
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.type(await screen.findByLabelText("Your message"), "And now?{Enter}");
    expect(await screen.findByText("Thinking it through")).toBeInTheDocument();
    const fresh = screen.getByRole("button", { name: "New Heinzelmännchen" });
    expect(fresh).toBeEnabled();
    await user.click(fresh);
    await waitFor(() => expect(sessionStorage.getItem("hz.chat")).toBe("fedcba9876543210"));
    expect(screen.queryByText("Thinking it through")).not.toBeInTheDocument();
    release();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(sessionStorage.getItem("hz.chat")).toBe("fedcba9876543210"); // the finished turn didn't pull the view back
  });

  it("says what a message may cost in one line, the rest in its info", async () => {
    mockApi(base);
    renderAt("/doctor");
    expect(await screen.findByText(/^Up to \$0\.25 per message · this chat \$0\.00$/)).toBeInTheDocument();
    expect(screen.getByTitle(/Runs your agent runner and may cost money/)).toBeInTheDocument();
  });

  it("groups and words what it lists", () => {
    expect(switcherGroups(sessions, all, "doctor").map((group) => group.label)).toEqual(["Earlier questions"]);
    expect(switcherGroups(sessions, all, "").map((group) => group.label)).toEqual([
      "Your Heinzelmännchen",
      "Questions",
      "Earlier questions",
    ]);
    // A session about a contract you no longer have is an earlier question, not a Heinzelmännchen.
    const gone = switcherGroups([{ ...weekly, context: contract("me:gone") }], [], "");
    expect(gone.map((group) => group.label)).toEqual(["Questions", "Earlier questions"]);
    expect([heinzelOf(contract("me:x")), heinzelOf({ kind: "route", ref: "/", detail: "" }), heinzelOf(null)]).toEqual([
      "me:x",
      null,
      null,
    ]);
    expect(ago("2026-10-24T08:00:00Z", Date.parse("2026-10-24T10:00:00Z"))).toBe("2 hours ago");
  });
});
