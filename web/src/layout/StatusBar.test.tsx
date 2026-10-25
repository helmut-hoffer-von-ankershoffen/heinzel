import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const answers = {
  ...shell,
  "/api/system/info": { version: "0.4.0", modules: [{ qualname: "clock" }] },
  "/api/clock": [
    { zone: "Europe/Berlin", time: "Fri 17:04", hours: [9, 18], working: true },
    { zone: "America/New_York", time: "Fri 11:04", hours: [9, 18], working: true },
  ],
};

describe("The status bar's clock", () => {
  it("keeps the zones open while the pointer moves up to them, and their link opens the clock's settings", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    const { container } = renderAt("/doctor");
    const clock = await screen.findByRole("button", { description: "Time for the team and stakeholders" });
    const wrapper = clock.parentElement as HTMLElement;
    fireEvent.mouseEnter(wrapper);
    const zones = await screen.findByRole("region", { name: "Time for the team and stakeholders" });
    expect(await within(zones).findByText("New York")).toBeInTheDocument();
    // The first zone is yours: the clock shows its time, and the panel lists it first, marked, then the others, all on
    // the 24-hour clock.
    const rows = within(zones).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0] as HTMLElement).getByText("Berlin")).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText("Primary")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText("New York")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).queryByText("Primary")).not.toBeInTheDocument();
    for (const row of rows) expect(row.textContent).toMatch(/\d{2}:\d{2}$/);
    const time = clock.querySelector("time") as HTMLTimeElement;
    expect(time).toHaveTextContent(/^\d{2}:\d{2}:\d{2}$/);
    expect(time.textContent).not.toMatch(/[ap]m/i);
    // Leaving the clock for the panel above: the panel is inside the hovered element, so nothing closes.
    fireEvent.mouseLeave(wrapper);
    fireEvent.mouseEnter(wrapper);
    await new Promise((resolve) => setTimeout(resolve, 250));
    await user.click(within(zones).getByRole("link", { name: "Edit time zones and working hours…" }));
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("closes a short while after the pointer leaves, opens pinned on click, and closes on Escape", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor");
    const clock = await screen.findByRole("button", { description: "Time for the team and stakeholders" });
    const wrapper = clock.parentElement as HTMLElement;
    fireEvent.mouseEnter(wrapper);
    fireEvent.mouseLeave(wrapper);
    expect(screen.getByRole("region", { name: "Time for the team and stakeholders" })).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(screen.queryByRole("region", { name: "Time for the team and stakeholders" })).not.toBeInTheDocument();
    await user.click(clock);
    expect(clock).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(clock).toHaveAttribute("aria-expanded", "false");
  });
});
