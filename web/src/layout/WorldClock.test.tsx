import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { WorldClock } from "./WorldClock";

const { create, destroy, update } = vi.hoisted(() => ({ create: vi.fn(), destroy: vi.fn(), update: vi.fn() }));
vi.mock("cobe", () => ({ default: create }));
const zones = ["Europe/Berlin", "America/New_York", "UTC"].map((zone) => ({
  zone,
  hours: [9, 18],
  working: false,
  time: "",
}));
const answers = {
  ...shell,
  "/api/system/info": { version: "0.7.1", modules: [{ qualname: "clock" }] },
  "/api/clock": zones,
};
function show() {
  mockApi(answers);
  return render(
    <Providers client={queryClient()}>
      <MemoryRouter>
        <WorldClock onClose={vi.fn()} />
      </MemoryRouter>
    </Providers>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("World Clock", () => {
  it("shows every zone in order, marks yours, keeps UTC readable without a marker and uses 24 hours", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const { container } = show();
    expect(await screen.findByText("Berlin · Yours")).toBeInTheDocument();
    expect(screen.getByText("UTC · No map location")).toBeInTheDocument();
    const list = screen.getByRole("list");
    expect(
      within(list)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([expect.stringContaining("Berlin"), expect.stringContaining("New York"), expect.stringContaining("UTC")]);
    for (const time of container.querySelectorAll("time")) expect(time.textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(screen.getByText(/Static map/)).toBeInTheDocument();
    await expectAccessible(container);
  });
  it("shows the shared next-work status after the autumn DST weekend despite stale server work flags", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-25T11:00:00Z"));
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    show();
    const summary = await screen.findByText("Starts in 21h · 1d 19h since end");
    expect(summary).toHaveAttribute("title", "Mon–Fri 09:00–18:00 · Starts Mon 09:00");
  });
  it("keeps unknown browser zones in place without moving the primary label", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    mockApi({ ...answers, "/api/clock": [{ ...zones[0], zone: "Future/New_Zone" }, zones[1]] });
    render(
      <Providers client={queryClient()}>
        <MemoryRouter>
          <WorldClock onClose={vi.fn()} />
        </MemoryRouter>
      </Providers>,
    );
    expect(await screen.findByText("New Zone · Yours")).toBeInTheDocument();
    expect(screen.getByText("Time unavailable in this browser")).toBeInTheDocument();
    expect(screen.queryByText("New York · Yours")).not.toBeInTheDocument();
  });
  it("explains a missing clock plugin without leaking previously cached zones", async () => {
    const client = queryClient();
    client.setQueryData(["clock"], zones);
    mockApi({ ...answers, "/api/system/info": { version: "0.7.1", modules: [] } });
    render(
      <Providers client={client}>
        <MemoryRouter>
          <WorldClock onClose={vi.fn()} />
        </MemoryRouter>
      </Providers>,
    );
    expect(await screen.findByText("Add the clock plugin and choose your team's time zones.")).toBeInTheDocument();
    expect(screen.queryByText("Loading time zones…")).not.toBeInTheDocument();
    expect(screen.queryByText("Berlin · Yours")).not.toBeInTheDocument();
  });
  it("does not initialise WebGL under reduced motion", async () => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    vi.spyOn(window, "matchMedia").mockReturnValue({ ...media, matches: true });
    create.mockClear();
    show();
    await screen.findByText("Berlin · Yours");
    expect(create).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Rotate globe left" })).not.toBeInTheDocument();
  });
  it("updates rather than recreates the renderer when rotated, and destroys it on unmount", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as WebGLRenderingContext);
    create.mockClear();
    destroy.mockClear();
    update.mockClear();
    create.mockReturnValue({ destroy, update });
    const { unmount } = show();
    await screen.findByText("Berlin · Yours");
    const count = create.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "Rotate globe right" }));
    expect(create).toHaveBeenCalledTimes(count);
    expect(update).toHaveBeenCalledWith(expect.objectContaining({ phi: expect.any(Number) }));
    unmount();
    expect(destroy).toHaveBeenCalled();
  });
  it("exposes projected markers as keyboard accessible tooltip triggers", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as WebGLRenderingContext);
    create.mockReturnValue({ destroy, update });
    show();
    const marker = await screen.findByRole("button", { name: "Berlin · Europe/Berlin" });
    await userEvent.hover(marker);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Berlin · Europe/Berlin");
    marker.focus();
    expect(marker).toHaveAttribute("aria-describedby", expect.stringContaining("world-clock-marker"));
    update.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Rotate globe right" }));
    expect(update).toHaveBeenCalledWith(expect.objectContaining({ phi: expect.any(Number) }));
  });
  it("falls back when the WebGL context is lost", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as WebGLRenderingContext);
    create.mockReturnValue({ destroy, update });
    const { container } = show();
    await screen.findByText("Berlin · Yours");
    const canvas = container.querySelector("canvas");
    expect(canvas).not.toBeNull();
    if (canvas) fireEvent(canvas, new Event("webglcontextlost"));
    expect(await screen.findByText(/Static map/)).toBeInTheDocument();
  });
  it("opens in the right panel by a deep link and closes back to the chat", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    mockApi(answers);
    renderAt("/?panel=world-clock");
    expect(await screen.findByRole("region", { name: "World Clock" })).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Chat" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close World Clock" }));
    expect(screen.getByRole("complementary", { name: "Chat" })).toBeInTheDocument();
  });
  it("repaints the asynchronously decoded map on the clock tick without a pointer event", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as WebGLRenderingContext);
    create.mockReturnValue({ destroy, update });
    show();
    await screen.findByText("Berlin · Yours");
    update.mockClear();
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(update).toHaveBeenCalledWith({ phi: expect.any(Number) });
  });
});
