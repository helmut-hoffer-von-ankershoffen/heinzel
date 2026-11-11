import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { chatKey, openChat, useChatSession, useChatSessions, useDecide } from "./chat";

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

describe("The chat's operations", () => {
  it("opens a session about a context with what the opener knows", async () => {
    const { calls } = mockApi({ "/api/system/runner/chat/open": { id: "s1" } });
    await openChat("event:42", "A job failed");
    const url = new URL(calls[0]?.url ?? "");
    expect(url.searchParams.get("context")).toBe("event:42");
    expect(url.searchParams.get("detail")).toBe("A job failed");
  });

  it("sends no detail when the opener has none", async () => {
    const { calls } = mockApi({ "/api/system/runner/chat/open": { id: "s1" } });
    await openChat(null, "");
    const url = new URL(calls[0]?.url ?? "");
    expect(url.searchParams.has("context")).toBe(false);
    expect(url.searchParams.has("detail")).toBe(false);
  });

  it("reads no session until it has an id", () => {
    const { calls } = mockApi({});
    const { wrapper } = setup();
    const { result } = renderHook(() => useChatSession(null), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
    expect(calls).toHaveLength(0);
  });

  it("reads the session by id", async () => {
    const { calls } = mockApi({ "/api/system/runner/chat/show": { id: "s9" } });
    const { wrapper } = setup();
    const { result } = renderHook(() => useChatSession("s9"), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual({ id: "s9" }));
    expect(new URL(calls[0]?.url ?? "").searchParams.get("session")).toBe("s9");
  });

  it("lists the sessions only when the switcher is open", async () => {
    const { calls } = mockApi({ "/api/system/runner/chat/list": [] });
    const { wrapper } = setup();
    renderHook(() => useChatSessions(false), { wrapper });
    expect(calls).toHaveLength(0);
    const open = renderHook(() => useChatSessions(true), { wrapper });
    await waitFor(() => expect(open.result.current.data).toEqual([]));
  });
});

describe("Deciding on a proposed action", () => {
  it("confirms or declines through different operations and caches the answered session", async () => {
    const { calls } = mockApi({
      "/api/system/runner/chat/confirm": { id: "s1", state: "confirmed" },
      "/api/system/runner/chat/decline": { id: "s2", state: "declined" },
    });
    const { client, wrapper } = setup();
    const { result } = renderHook(() => useDecide(), { wrapper });
    await result.current.mutateAsync({ session: "s1", action: "a1", confirm: true });
    await result.current.mutateAsync({ session: "s2", action: "a2", confirm: false });
    expect(calls.map((c) => new URL(c.url).pathname)).toEqual([
      "/api/system/runner/chat/confirm",
      "/api/system/runner/chat/decline",
    ]);
    expect(new URL(calls[0]?.url ?? "").searchParams.get("action")).toBe("a1");
    expect(client.getQueryData(chatKey("s1"))).toEqual({ id: "s1", state: "confirmed" });
    expect(client.getQueryData(chatKey("s2"))).toEqual({ id: "s2", state: "declined" });
  });
});
