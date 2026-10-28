import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import type { Resources } from "@/api/resources";
import { expectAccessible, mockApi } from "@/test/render";
import { ResourceStatus } from "./ResourceStatus";

const sample: Resources = {
  sampled_at: "2026-10-25T12:00:00Z",
  cpu_percent: 12.5,
  memory_percent: 40,
  load_1m: 3,
  logical_cores: 10,
  problems: [],
};
function show(report: unknown = sample) {
  sessionStorage.setItem("hz.token", "test-token");
  const calls = mockApi({ "/api/system/resource": report });
  const client = queryClient();
  return {
    ...calls,
    client,
    ...render(
      <Providers client={client}>
        <MemoryRouter>
          <ResourceStatus />
        </MemoryRouter>
      </Providers>,
    ),
  };
}
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("footer system resources", () => {
  it("shows one compact accessible trigger and measured details with Doctor and bearer auth", async () => {
    const { container, calls } = show();
    const trigger = await screen.findByRole("button", { name: "CPU Mem Load: CPU 13%, mem 40%, load 3.00, OK" });
    expect(trigger.closest("[data-state]")).toHaveAttribute("data-state", "ok");
    expect(trigger.textContent).toBe("");
    const bars = trigger.querySelectorAll("[data-state]");
    expect(bars).toHaveLength(3);
    for (const [index, height] of ["12.5%", "40%", "15%"].entries()) {
      expect(bars[index]?.firstElementChild).toHaveStyle({ height });
    }
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    fireEvent.focus(trigger);
    expect(screen.getByRole("region")).toHaveTextContent("1-minute load · 10 logical cores");
    expect(screen.getByRole("link", { name: "Open Doctor" })).toHaveAttribute("href", "/doctor");
    expect(screen.getByRole("region")).toHaveTextContent(new Date(sample.sampled_at).toLocaleString());
    expect(calls[0]?.headers.get("Authorization")).toBe("Bearer test-token");
    await expectAccessible(container);
  });
  it.each([
    [{ cpu_percent: 90 }, "bad"],
    [{ memory_percent: 90 }, "bad"],
    [{ cpu_percent: 75 }, "warn"],
    [{ load_1m: 20 }, "warn"],
    [{ load_1m: 20.01 }, "bad"],
    [{ load_1m: 10 }, "ok"],
    [{ load_1m: null }, "warn"],
  ])("distinguishes load capacity and percent thresholds: %j", async (changes, state) => {
    show({ ...sample, ...changes });
    const trigger = await screen.findByRole("button", { name: /CPU Mem Load: CPU (13|90|75)%/ });
    expect(trigger.closest("[data-state]")).toHaveAttribute("data-state", state);
  });
  it("never presents unavailable readings as healthy zeroes", async () => {
    show({
      ...sample,
      cpu_percent: null,
      memory_percent: null,
      load_1m: null,
      logical_cores: null,
      problems: ["CPU usage is unavailable."],
    });
    const trigger = await screen.findByRole("button", {
      name: "CPU Mem Load: CPU Unavailable, mem Unavailable, load Unavailable, off",
    });
    fireEvent.click(trigger);
    expect(trigger.closest("[data-state]")).toHaveAttribute("data-state", "off");
    expect(await screen.findByText("CPU usage is unavailable.")).toBeInTheDocument();
    expect(screen.getByRole("region")).not.toHaveTextContent("0%");
  });
  it("replaces stale green readings when a refresh fails", async () => {
    const { client } = show();
    await screen.findByRole("button", { name: /CPU 13%/ });
    mockApi({ "/api/system/resource": { status: 500, body: { error: "offline" } } });
    await act(() => client.refetchQueries({ queryKey: ["system", "resource"] }));
    const trigger = await screen.findByRole("button", { name: /CPU Unavailable/ });
    fireEvent.click(trigger);
    expect(trigger.closest("[data-state]")).toHaveAttribute("data-state", "off");
    expect(screen.getByRole("status")).toHaveTextContent("could not be refreshed");
  });
  it("pauses requests while hidden and refreshes immediately on return", async () => {
    let visible = "hidden";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visible as DocumentVisibilityState);
    const { calls } = show();
    expect(calls).toHaveLength(0);
    visible = "visible";
    fireEvent(document, new Event("visibilitychange"));
    await screen.findByRole("button", { name: /CPU 13%/ });
    expect(calls).toHaveLength(1);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
    visible = "hidden";
    fireEvent(document, new Event("visibilitychange"));
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(calls).toHaveLength(1);
    vi.useRealTimers();
    visible = "visible";
    fireEvent(document, new Event("visibilitychange"));
    await waitFor(() => expect(calls).toHaveLength(2));
  });
  it("samples every three seconds while visible and stops after unmount", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { calls, unmount } = show();
    await screen.findByRole("button", { name: /CPU 13%/ });
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(calls).toHaveLength(2);
    unmount();
    const count = calls.length;
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(calls).toHaveLength(count);
  });
  it.each([-1, 101, "12"])("refuses malformed readings instead of drawing a healthy bar: %s", async (value) => {
    show({ ...sample, cpu_percent: value });
    const trigger = await screen.findByRole("button", { name: /CPU Unavailable/ });
    fireEvent.click(trigger);
    expect(await screen.findByRole("status")).toHaveTextContent("could not be refreshed");
  });
});
