import { act, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const names = ["Europe/Berlin", "America/New_York", "Asia/Tokyo"];
const answers = (zones = names, shown = true) => ({
  ...shell,
  "/api/system/info": { version: "0.7.2", modules: [{ qualname: "clock" }] },
  "/api/clock": zones.map((zone) => ({ zone, time: "unused", hours: [9, 18], working: true })),
  "/api/system/ui/status": [
    {
      id: "clock:primary-zone",
      module: "clock",
      plugin: "heinzel-clock",
      order: 900,
      refresh: 60,
      problem: null,
      slots: { sidebar: shown },
      item: { text: "Berlin", tone: "neutral", icon: "clock" },
    },
  ],
});
afterEach(() => vi.useRealTimers());
describe("one continuous zone ticker", () => {
  it("keeps every zone primary first with 24-hour time and configured work status in one accessible label", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-19T10:00:00Z"));
    mockApi(answers());
    const { container } = renderAt("/doctor");
    const ticker = await screen.findByTestId("zone-ticker");
    expect(ticker).toHaveAccessibleName(
      /^World clock: Berlin 12:00:00 · Working · 3h since start · 6h left · New York 06:00:00 · Starts in 3h .*Tokyo 19:00:00 · Starts in 14h/,
    );
    expect(ticker).toHaveAttribute("href", "/doctor?panel=world-clock");
    expect(ticker.querySelector("svg")).toBeNull();
    const track = ticker.querySelector("[aria-hidden]");
    expect(track?.children).toHaveLength(2);
    expect(track?.children[0]?.textContent).toBe(track?.children[1]?.textContent);
    await expectAccessible(container);
  });
  it("preserves an unknown browser zone and its primary position", async () => {
    mockApi(answers(["Unknown/Zone", ...names]));
    renderAt("/doctor");
    expect(await screen.findByTestId("zone-ticker")).toHaveAccessibleName(
      /^World clock: Zone Time unavailable.*Berlin/,
    );
  });
  it("is absent when its sidebar placement is off", async () => {
    mockApi(answers(names, false));
    renderAt("/doctor");
    await screen.findByRole("navigation");
    expect(screen.queryByTestId("zone-ticker")).not.toBeInTheDocument();
  });
  it("keeps ticking seconds while the strip is focused for reading", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(new Date("2026-10-19T10:00:00Z"));
    mockApi(answers());
    renderAt("/doctor");
    const ticker = await screen.findByTestId("zone-ticker");
    ticker.focus();
    expect(ticker).toHaveAccessibleName(/^World clock: Berlin 12:00:00/);
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(ticker).toHaveAccessibleName(/^World clock: Berlin 12:00:01/);
  });
});
