// The popup follows the server even when the setting is switched off and on while a poll is on its way (ADR 0116).
import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { keys } from "@/api/queries";
import { mockApi } from "@/test/render";
import { AutoUpdate, type AutoUpdateStatus } from "./AutoUpdate";
import { KEY } from "./autoUpdateApi";
import { updateTransport } from "./controller";

afterEach(() => vi.restoreAllMocks());

const idle: AutoUpdateStatus = {
  enabled: true,
  reason: null,
  phase: "idle",
  version: "1.0.0",
  core: null,
  plugins: {},
  seconds_left: null,
  apply_at: null,
  next_window_at: null,
  postponements: 0,
  message: null,
};
const setting = (value: boolean) => ({
  files: [],
  plugins: [],
  settings: [{ section: "hz", key: "auto_update", value, default: true, source: "user", env: "HZ_AUTO_UPDATE" }],
});

it("shows the countdown after the setting went off and on, though a poll saw it off in between", async () => {
  let server: AutoUpdateStatus = idle;
  let config = setting(true);
  const client = queryClient();
  sessionStorage.setItem("hz.token", "test-token");
  mockApi({ "/api/auto-update": () => server, "/api/system/config/show": () => config });
  render(
    <Providers client={client}>
      <AutoUpdate />
    </Providers>,
  );
  await vi.waitFor(() => expect(client.getQueryData(keys.config)).toBeDefined());
  // Turned off: the server says so at once; this window's poll sees it before its own copy of the setting changes.
  server = { ...idle, phase: "off", enabled: false, reason: "Automatic updates are off." };
  await new Promise((resolve) => setTimeout(resolve, 2300));
  config = setting(false);
  await act(() => client.invalidateQueries({ queryKey: keys.config }));
  // Turned on again, and a release is announced: the countdown must show within a poll or two.
  server = { ...idle, phase: "countdown", core: "9.9.9", seconds_left: 30, apply_at: "2026-11-02T09:00:30Z" };
  config = setting(true);
  await act(() => client.invalidateQueries({ queryKey: keys.config }));
  expect(await screen.findByRole("alertdialog", {}, { timeout: 5000 })).toHaveTextContent("hz updates itself in");
}, 15000);

it("shows a countdown enabled in another window while this window still has the off setting cached", async () => {
  let server: AutoUpdateStatus = { ...idle, phase: "off", enabled: false, reason: "Automatic updates are off." };
  const config = setting(false);
  const client = queryClient();
  sessionStorage.setItem("hz.token", "test-token");
  mockApi({ "/api/auto-update": () => server, "/api/system/config/show": () => config });
  render(
    <Providers client={client}>
      <AutoUpdate />
    </Providers>,
  );
  await vi.waitFor(() => expect(client.getQueryData(keys.config)).toEqual(config));
  await vi.waitFor(() => expect(client.getQueryData(KEY)).toEqual(server));
  // Another window or the CLI enables updates. This window gets no config invalidation or focus event.
  server = { ...idle, phase: "countdown", core: "9.9.9", seconds_left: 30, apply_at: "2026-11-02T09:00:30Z" };
  expect(await screen.findByRole("alertdialog", {}, { timeout: 5000 })).toHaveTextContent("hz updates itself in");
  expect(client.getQueryData(keys.config)).toEqual(config);
}, 10000);

it("recovers from a status request stalled across restart and reloads the updated server", async () => {
  vi.useFakeTimers();
  const reload = vi.spyOn(updateTransport, "reload").mockImplementation(() => undefined);
  vi.spyOn(updateTransport, "clean").mockResolvedValue(undefined);
  const client = queryClient();
  client.setQueryData(KEY, idle);
  let calls = 0;
  mockApi({
    "/api/system/config/show": setting(true),
    "/api/auto-update": (request: Request) => {
      calls += 1;
      if (calls === 1)
        return new Promise((_resolve, reject) => {
          request.signal.addEventListener("abort", () => reject(request.signal.reason), { once: true });
        });
      return { ...idle, version: "1.1.0", message: "hz is up to date: 1.1.0, updated automatically." };
    },
  });
  try {
    render(
      <Providers client={client}>
        <AutoUpdate />
      </Providers>,
    );
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(calls).toBe(1);
    // The replacement is ready, but the retiring server's response never reaches this browser.
    await act(() => vi.advanceTimersByTimeAsync(8_000));
    expect(reload).toHaveBeenCalledOnce();
  } finally {
    client.clear();
    vi.useRealTimers();
  }
});

it("shows a slow valid countdown even when the additional status read fails", async () => {
  vi.useFakeTimers();
  const client = queryClient();
  client.setQueryData(KEY, idle);
  let calls = 0;
  mockApi({
    "/api/system/config/show": setting(true),
    "/api/auto-update": (request: Request) => {
      calls += 1;
      if (calls === 2) return { status: 503, body: { error: "Restarting." } };
      return new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => resolve({ ...idle, phase: "countdown", core: "9.9.9", seconds_left: 30 }),
          5_000,
        );
        request.signal.addEventListener(
          "abort",
          () => {
            clearTimeout(timer);
            reject(request.signal.reason);
          },
          { once: true },
        );
      });
    },
  });
  try {
    render(
      <Providers client={client}>
        <AutoUpdate />
      </Providers>,
    );
    await act(() => vi.advanceTimersByTimeAsync(7_500));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Installing: hz 9.9.9.");
    expect(screen.getByRole("button", { name: "Postpone 1 hour" })).toBeEnabled();
  } finally {
    client.clear();
    vi.useRealTimers();
  }
});
