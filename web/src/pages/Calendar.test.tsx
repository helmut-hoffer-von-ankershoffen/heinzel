import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { joinLink } from "@/pages/Calendar";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const at = (hour: number) => `2026-10-26T${String(hour).padStart(2, "0")}:00:00+00:00`;
const event = (id: string, title: string, hour: number, extra: object = {}) => ({
  id,
  title,
  start: at(hour),
  end: at(hour + 1),
  all_day: false,
  organizer: null,
  attendees: [{ name: "Grace", email: "grace@example.com" }],
  status: "confirmed",
  conference_id: null,
  conference_url: null,
  url: null,
  location: null,
  response: null,
  organized_by_user: false,
  ...extra,
});
const today = {
  since: at(0),
  until: "2026-10-27T00:00:00+00:00",
  now: at(9),
  events: [
    event("standup", "Standup", 9, { organized_by_user: true, conference_url: "https://meet.google.com/abc-defg-hij" }),
    event("one", "1:1", 11, { response: "pending", conference_url: "javascript:alert(1)" }),
  ],
};
const countdown = { now: at(9), current: null, next: null, line: "Next: 1:1 in 25 min", problem: null, fix: null };

describe("Calendar", () => {
  it("lists today's events with the join link, your answer and the guests", async () => {
    mockApi({ ...shell, "/api/calendar": today, "/api/calendar/now": countdown });
    const { container } = renderAt("/calendar");
    const standup = (await screen.findByText("Standup")).closest("li") as HTMLElement;
    expect(within(standup).getByRole("link", { name: "Join" })).toHaveAttribute(
      "href",
      "https://meet.google.com/abc-defg-hij",
    );
    expect(within(standup).getByText("yours")).toBeInTheDocument();
    const one = screen.getByText("1:1").closest("li") as HTMLElement;
    expect(within(one).queryByRole("link", { name: "Join" })).toBeNull(); // not an https link
    expect(within(one).getByText("not answered")).toBeInTheDocument();
    expect(within(one).getByText("with Grace")).toBeInTheDocument();
    expect(await screen.findByText("Next: 1:1 in 25 min")).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("answers an invitation only after the change it shows is confirmed", async () => {
    const shown = {
      done: false,
      summary: "Would answer '1:1' with declined.",
      confirm: "hz calendar respond one declined --confirm",
      event: null,
    };
    const { calls } = mockApi({
      ...shell,
      "/api/calendar": today,
      "/api/calendar/now": countdown,
      "/api/calendar/respond": shown,
    });
    const user = userEvent.setup();
    renderAt("/calendar");
    const one = (await screen.findByText("1:1")).closest("li") as HTMLElement;
    await user.click(within(one).getByRole("button", { name: "Decline" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(shown.summary)).toBeInTheDocument();
    const answers = () => calls.filter((call) => new URL(call.url).pathname === "/api/calendar/respond");
    expect(answers().map((call) => new URL(call.url).searchParams.get("confirm"))).toEqual(["false"]);
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(answers()).toHaveLength(2));
    expect(new URL(answers()[1]?.url ?? "").searchParams.get("confirm")).toBe("true");
  });

  it("shows only https join links", () => {
    expect(joinLink("https://zoom.us/j/1")).toBe("https://zoom.us/j/1");
    expect(joinLink("javascript:alert(1)")).toBeNull();
    expect(joinLink(null)).toBeNull();
  });
});
