// What reports to the page's splash: failed calls, the notice stream, a restart and the theme.
import { render, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { ApiError } from "@/api/client";
import { useRestartServer } from "@/api/queries";
import { ThemeTokens } from "@/layout/ThemeTokens";
import { follow } from "@/notifications/stream";
import { acmeTheme, themeList } from "@/test/fixtures";
import { mockApi } from "@/test/render";
import { readSnapshot } from "./snapshot";
import { splash } from "./store";

describe("what tells the splash", () => {
  it("a call that couldn't reach hz makes it check, a refused one doesn't", async () => {
    const client = queryClient();
    for (const [key, error] of [
      ["network", new TypeError("Failed to fetch")],
      ["proxy", new ApiError(502, "Bad Gateway")],
      ["token", new ApiError(401, "refused")],
    ] as const) {
      await client
        .fetchQuery({ queryKey: [key], queryFn: () => Promise.reject(error), retry: false })
        .catch(() => undefined);
    }
    expect(splash.get().checks).toBe(2);
  });

  it("the notice stream breaking makes it check", async () => {
    sessionStorage.setItem("hz.token", "test-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    const controller = new AbortController();
    await follow(() => undefined, {
      popups: false,
      signal: controller.signal,
      wait: async () => controller.abort(),
    });
    expect(splash.get().checks).toBe(1);
  });

  it("a restart shows its line, and the fix when hz didn't come back", async () => {
    mockApi({
      "/api/restart": { status: 202, body: { restarting: true, message: "Restarting" } },
      "/api/system/info": { status: 502, body: {} },
    });
    const reload = vi.fn();
    const { result } = renderHook(() => useRestartServer({ reload, interval: 1 }), {
      wrapper: ({ children }) => <Providers client={queryClient()}>{children}</Providers>,
    });
    const sent = vi.spyOn(splash, "send");
    result.current.mutate();
    await waitFor(() => expect(splash.get().phase).toBe("error"));
    expect(sent.mock.calls.map(([event]) => event.type)).toEqual(["restart", "restart-failed"]);
    expect(splash.get().problem).toBe("unreachable");
    expect(reload).not.toHaveBeenCalled();
  });

  it("a restart hz refuses leaves the app as it was", async () => {
    mockApi({ "/api/restart": { status: 400, body: { error: "This server can't restart itself" } } });
    const { result } = renderHook(() => useRestartServer({ reload: vi.fn(), interval: 1 }), {
      wrapper: ({ children }) => <Providers client={queryClient()}>{children}</Providers>,
    });
    result.current.mutate();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(splash.get().phase).not.toBe("restarting");
  });

  it("the theme applied lets it go and is kept for the next start", async () => {
    sessionStorage.setItem("hz.token", "test-token");
    mockApi({ "/api/system/theme/list": themeList("acme") });
    render(
      <Providers client={queryClient()}>
        <ThemeTokens />
      </Providers>,
    );
    await waitFor(() => expect(splash.get().themed).toBe(true));
    expect(readSnapshot().light?.background).toBe(acmeTheme.light.background);
  });

  it("a theme list hz failed to give lets it go too, but not one hz couldn't be reached for", async () => {
    sessionStorage.setItem("hz.token", "test-token");
    mockApi({ "/api/system/theme/list": { status: 500, body: { error: "broken" } } });
    const first = render(
      <Providers client={queryClient()}>
        <ThemeTokens />
      </Providers>,
    );
    await waitFor(() => expect(splash.get().themed).toBe(true), { timeout: 3_000 }); // after the one retry
    first.unmount();
    splash.reset();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    const client = queryClient();
    render(
      <Providers client={client}>
        <ThemeTokens />
      </Providers>,
    );
    await waitFor(() => expect(client.getQueryState(["system", "theme", "list"])?.status).toBe("error"), {
      timeout: 3_000,
    });
    expect(splash.get().themed).toBe(false);
  });
});
