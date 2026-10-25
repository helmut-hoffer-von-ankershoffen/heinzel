import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const entry = { module: "clock", plugin: "heinzel-clock", order: 900, refresh: 60, problem: null };
const zone = (name: string) => ({ zone: name, time: "x", hours: [9, 18], working: true });
const answers = (zones: string[], sidebar = true) => ({
  ...shell,
  "/api/system/info": { version: "0.7.1", modules: [{ qualname: "clock" }] },
  "/api/clock": zones.map(zone),
  "/api/system/ui/status": [
    {
      ...entry,
      id: "clock:primary-zone",
      slots: { footer: true, sidebar },
      item: { text: "Berlin", tone: "neutral", icon: "clock", live: { kind: "clock", zone: "Europe/Berlin" } },
    },
  ],
});
const THREE = ["Europe/Berlin", "America/New_York", "Asia/Tokyo"];

function reducedMotion(reduce: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: reduce && query.includes("reduce"),
    media: query,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    onchange: null,
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

const ticker = async () => (await screen.findByTestId("zone-ticker")) as HTMLElement;
const shownText = (element: HTMLElement) => element.querySelector("span[aria-hidden]")?.textContent;

describe("The sidebar's zone ticker", () => {
  beforeEach(() => reducedMotion(false));
  afterEach(() => vi.useRealTimers());

  it("goes through every zone two at a time, primary first, on the 24-hour clock", async () => {
    mockApi(answers(THREE));
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { container } = renderAt("/doctor");
    const element = await ticker();
    expect(shownText(element)).toMatch(/^Berlin \d{2}:\d{2} · New York \d{2}:\d{2}$/);
    expect(element.textContent).not.toMatch(/[ap]m/i);
    expect(element).toHaveAttribute("title", expect.stringMatching(/Berlin .* · New York .* · Tokyo \d{2}:\d{2}/));
    await act(() => vi.advanceTimersByTimeAsync(4100));
    expect(shownText(element)).toMatch(/^Tokyo \d{2}:\d{2}$/);
    await act(() => vi.advanceTimersByTimeAsync(4000));
    expect(shownText(element)).toMatch(/^Berlin /);
    vi.useRealTimers();
    await expectAccessible(container);
  });

  it("rests while the pointer or focus is on it", async () => {
    mockApi(answers(THREE));
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    renderAt("/doctor");
    const element = await ticker();
    fireEvent.mouseEnter(element);
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(shownText(element)).toMatch(/^Berlin /);
    fireEvent.mouseLeave(element);
    fireEvent.focus(element);
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(shownText(element)).toMatch(/^Berlin /);
    fireEvent.blur(element);
    await act(() => vi.advanceTimersByTimeAsync(4100));
    expect(shownText(element)).toMatch(/^Tokyo /);
  });

  it("shows all zones at once, without motion, when reduced motion is asked for", async () => {
    reducedMotion(true);
    mockApi(answers(THREE));
    const { container } = renderAt("/doctor");
    const element = await ticker();
    expect(shownText(element)).toMatch(/Berlin .* · New York .* · Tokyo \d{2}:\d{2}$/);
    expect(element.querySelector("span[aria-hidden]")).not.toHaveClass("truncate");
    await expectAccessible(container);
  });

  it("shows a single zone plainly", async () => {
    mockApi(answers(["Europe/Berlin"]));
    renderAt("/doctor");
    const element = await ticker();
    expect(shownText(element)).toMatch(/^Berlin \d{2}:\d{2}$/);
    expect(element.querySelector("span[aria-hidden]")?.className).not.toMatch(/animate/);
  });

  it("is not there unless the clock item is turned on for the sidebar", async () => {
    mockApi(answers(THREE, false));
    renderAt("/doctor");
    await screen.findByRole("navigation");
    expect(screen.queryByTestId("zone-ticker")).not.toBeInTheDocument();
  });
});

describe("World Clock naming", () => {
  it("names the sidebar ticker and shows the globe", async () => {
    mockApi(answers(["Europe/Berlin"]));
    renderAt("/doctor");
    const element = await ticker();
    expect(element).toHaveTextContent("World Clock: Berlin");
    expect(element.querySelector("svg.lucide-globe")).not.toBeNull();
  });
});
