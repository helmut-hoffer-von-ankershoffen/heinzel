import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { useFooterHealth } from "./resources";

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

function visibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("The footer's health", () => {
  afterEach(() => visibility("visible"));

  it("reads the doctor's report while the page is visible", async () => {
    mockApi({ "/api/system/doctor": { status: "up", checks: {} } });
    const { result } = renderHook(() => useFooterHealth(), { wrapper });
    await waitFor(() => expect(result.current.data?.status).toBe("up"));
  });

  it("does not read anything while the page is hidden, and reads again when it shows", async () => {
    const { calls } = mockApi({ "/api/system/doctor": { status: "up", checks: {} } });
    visibility("hidden");
    const { result } = renderHook(() => useFooterHealth(), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
    act(() => visibility("visible"));
    await waitFor(() => expect(result.current.data?.status).toBe("up"));
    expect(calls).toHaveLength(1);
  });
});
