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

describe("World clock", () => {
  it("shows city names in order, marks the primary, keeps UTC readable and uses 24 hours", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const { container } = show();
    expect(await screen.findByText("Primary")).toBeInTheDocument();
    expect(screen.getByText("Berlin")).toBeInTheDocument();
    const list = screen.getByRole("list");
    expect(within(list).getByText("Primary")).toBeInTheDocument();
    expect(
      within(list).getAllByRole("img", { name: /Working hours|Off duty|Likely sleeping|Work state unavailable/ }),
    ).toHaveLength(3);
    expect(
      within(list)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([expect.stringContaining("Berlin"), expect.stringContaining("New York"), expect.stringContaining("UTC")]);
    expect(within(list).queryByText("Europe/Berlin")).not.toBeInTheDocument();
    expect(within(list).queryByText("America/New_York")).not.toBeInTheDocument();
    for (const time of container.querySelectorAll("time")) expect(time.textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(screen.getByText(/Static map/)).toBeInTheDocument();
    await expectAccessible(container);
  });
  it("shows the shared next-work status after the autumn DST weekend despite stale server work flags", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-25T11:00:00Z"));
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    show();
    const state = (await screen.findAllByRole("img", { name: "Off duty · time for fun" }))[0];
    expect(state).toHaveAttribute(
      "title",
      "Off duty · time for fun · Starts in 21h · 1d 19h since end · Mon–Fri 09:00–18:00 · Starts Mon 09:00",
    );
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
    expect(await screen.findByText("Primary")).toBeInTheDocument();
    expect(screen.getByText("New Zone")).toBeInTheDocument();
    expect(screen.getByText("Time unavailable in this browser")).toBeInTheDocument();
    const newYork = screen.getByText("New York").closest("li");
    expect(newYork).not.toBeNull();
    expect(within(newYork as HTMLElement).queryByText("Primary")).not.toBeInTheDocument();
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
    expect(screen.queryByText("Berlin · Primary")).not.toBeInTheDocument();
  });
  it("does not initialise WebGL under reduced motion", async () => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    vi.spyOn(window, "matchMedia").mockReturnValue({ ...media, matches: true });
    create.mockClear();
    show();
    await screen.findByText("Berlin");
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
    await screen.findByText("Berlin");
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
    const marker = await screen.findByRole("button", { name: "Berlin" });
    await userEvent.hover(marker);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(/^Berlin$/);
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
    await screen.findByText("Berlin");
    const canvas = container.querySelector("canvas");
    expect(canvas).not.toBeNull();
    if (canvas) fireEvent(canvas, new Event("webglcontextlost"));
    expect(await screen.findByText(/Static map/)).toBeInTheDocument();
  });
  it("opens in the right panel by a deep link and closes back to the chat", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    mockApi(answers);
    renderAt("/?panel=world-clock");
    expect(await screen.findByRole("region", { name: "World clock" })).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Chat" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close World clock" }));
    expect(screen.getByRole("complementary", { name: "Chat" })).toBeInTheDocument();
  });
  it("repaints the asynchronously decoded map on the clock tick without a pointer event", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as WebGLRenderingContext);
    create.mockReturnValue({ destroy, update });
    show();
    await screen.findByText("Berlin");
    update.mockClear();
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(update).toHaveBeenCalledWith({ phi: expect.any(Number) });
  });
});
