import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const zoe = { name: "Zoe", email: "zoe@example.com" };
const message = (id: string, subject: string, labels: string[] = ["INBOX", "UNREAD"]) => ({
  id,
  thread: `t-${id}`,
  sender: zoe,
  recipients: [],
  subject,
  received_at: "2026-10-26T09:00:00+00:00",
  labels,
  url: null,
});
const inbox = {
  since: "2026-10-19T09:00:00+00:00",
  until: "2026-10-26T10:00:00+00:00",
  messages: [message("m2", "Budget due"), message("m1", "Lunch?")],
  important: ["m2"],
};
const conversation = {
  id: "t-m1",
  subject: "Lunch?",
  messages: [
    { message: message("m1", "Lunch?"), text: '<img src="https://tracker.example/p.gif"> Noon? [email]', headers: {} },
  ],
};

describe("Mail", () => {
  it("lists unread mail, the important marked, and shows a conversation as plain text", async () => {
    mockApi({ ...shell, "/api/mail": inbox, "/api/mail/show": conversation });
    const user = userEvent.setup();
    const { container } = renderAt("/mail");
    const budget = (await screen.findByText("Budget due")).closest("li") as HTMLElement;
    expect(within(budget).getByText("important")).toBeInTheDocument();
    await user.click(screen.getByText("Lunch?"));
    expect(await screen.findByText(/<img src=/)).toBeInTheDocument(); // as text: nothing loaded
    expect(container.querySelector("img")).toBeNull();
    await expectAccessible(container);
  });

  it("archives only after the change it shows is confirmed", async () => {
    const shown = {
      done: false,
      summary: "Would file conversation t-m1, removing inbox.",
      confirm: "x",
      draft: null,
      sent: null,
      labels: null,
    };
    const { calls } = mockApi({
      ...shell,
      "/api/mail": inbox,
      "/api/mail/show": conversation,
      "/api/mail/archive": shown,
    });
    const user = userEvent.setup();
    renderAt("/mail?thread=t-m1");
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(shown.summary)).toBeInTheDocument();
    const archives = () => calls.filter((call) => new URL(call.url).pathname === "/api/mail/archive");
    expect(archives().map((call) => new URL(call.url).searchParams.get("confirm"))).toEqual(["false"]);
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(archives()).toHaveLength(2));
    expect(new URL(archives()[1]?.url ?? "").searchParams.get("confirm")).toBe("true");
  });

  it("sends a kept reply only after confirming the whole draft it shows, that version only", async () => {
    const kept = {
      done: true,
      summary: "Kept a draft to zoe@example.com in your drafts.",
      confirm: null,
      draft: { id: "r-1", content: { message: message("m-d", "Re: Lunch?", ["DRAFT"]), text: "Noon.", headers: {} } },
      sent: null,
      labels: null,
    };
    const shown = { ...kept, done: false, summary: "Would send 'Re: Lunch?' to zoe@example.com:\nNoon.", confirm: "x" };
    const { calls } = mockApi({
      ...shell,
      "/api/mail": inbox,
      "/api/mail/show": conversation,
      "/api/mail/reply": kept,
      "/api/mail/send": shown,
    });
    const user = userEvent.setup();
    renderAt("/mail?thread=t-m1");
    await user.type(await screen.findByLabelText("Reply to Zoe"), "Noon.");
    await user.click(screen.getByRole("button", { name: "Keep as a draft" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm" }));
    await user.click(await screen.findByRole("button", { name: "Send it" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/Would send 'Re: Lunch\?' to zoe@example.com:/)).toBeInTheDocument();
    const sends = () => calls.filter((call) => new URL(call.url).pathname === "/api/mail/send");
    expect(sends()).toHaveLength(1);
    expect(new URL(sends()[0]?.url ?? "").searchParams.get("confirm")).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(sends()).toHaveLength(2));
    const confirmed = new URL(sends()[1]?.url ?? "").searchParams;
    expect([confirmed.get("draft"), confirmed.get("seen"), confirmed.get("confirm")]).toEqual(["r-1", "m-d", "true"]);
  });

  it("cancelling confirms nothing, and a failure says why", async () => {
    const { calls } = mockApi({
      ...shell,
      "/api/mail": inbox,
      "/api/mail/show": conversation,
      "/api/mail/archive": { status: 400, body: { detail: "Your sign-in expired." } },
    });
    const user = userEvent.setup();
    renderAt("/mail?thread=t-m1");
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(calls.filter((call) => new URL(call.url).searchParams.get("confirm") === "true")).toEqual([]);
  });

  it("says when there is no unread mail", async () => {
    mockApi({ ...shell, "/api/mail": { ...inbox, messages: [], important: [] } });
    renderAt("/mail");
    expect(await screen.findByText("No unread mail.")).toBeInTheDocument();
  });
});
