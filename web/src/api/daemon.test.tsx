import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { daemonStatusKey, useChangeDaemon, useDaemonStatus } from "./daemon";

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

const paths = (calls: Request[]) => calls.filter((c) => c.method === "POST").map((c) => new URL(c.url).pathname);

describe("Daemon controls", () => {
  it("calls the operation of the chosen action", async () => {
    const answers = Object.fromEntries(
      ["install", "uninstall", "pause", "resume", "restart"].map((a) => [`/api/system/daemon/${a}`, { paused: false }]),
    );
    const { calls } = mockApi({ ...answers, "/api/system/daemon/status": {} });
    const { wrapper } = setup();
    const { result } = renderHook(() => useChangeDaemon(), { wrapper });
    for (const action of ["install", "uninstall", "pause", "resume", "restart"] as const) {
      await result.current.mutateAsync(action);
    }
    expect(paths(calls)).toEqual(
      ["install", "uninstall", "pause", "resume", "restart"].map((a) => `/api/system/daemon/${a}`),
    );
  });

  it("shows the paused state at once from the answer, before the status is read again", async () => {
    mockApi({ "/api/system/daemon/pause": { paused: true }, "/api/system/daemon/status": () => new Promise(() => {}) });
    const { client, wrapper } = setup();
    client.setQueryData(daemonStatusKey, { paused: false, installed: true });
    const { result } = renderHook(() => useChangeDaemon(), { wrapper });
    await result.current.mutateAsync("pause");
    expect(client.getQueryData(daemonStatusKey)).toEqual({ paused: true, installed: true });
  });

  it("does not invent a status when none was read yet", async () => {
    mockApi({ "/api/system/daemon/pause": { paused: true }, "/api/system/daemon/status": () => new Promise(() => {}) });
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useChangeDaemon(), { wrapper });
    await result.current.mutateAsync("pause");
    expect(client.getQueryData(daemonStatusKey)).toBeUndefined();
  });

  it("marks the status and the UI status stale after every change, even a failed one", async () => {
    mockApi({ "/api/system/daemon/restart": { status: 500, body: { error: "no" } } });
    const { client, wrapper } = setup();
    client.setQueryData(daemonStatusKey, { paused: false });
    client.setQueryData(["ui", "status"], { x: 1 });
    const { result } = renderHook(() => useChangeDaemon(), { wrapper });
    await expect(result.current.mutateAsync("restart")).rejects.toThrow();
    expect(client.getQueryState(daemonStatusKey)?.isInvalidated).toBe(true);
    expect(client.getQueryState(["ui", "status"])?.isInvalidated).toBe(true);
  });
});

describe("The daemon's status", () => {
  it("is read from the daemon and not at all when disabled", async () => {
    const { calls } = mockApi({ "/api/system/daemon/status": { paused: false } });
    const { wrapper } = setup();
    const off = renderHook(() => useDaemonStatus(false), { wrapper });
    expect(off.result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
    const on = renderHook(() => useDaemonStatus(), { wrapper });
    await waitFor(() => expect(on.result.current.data).toEqual({ paused: false }));
  });
});
