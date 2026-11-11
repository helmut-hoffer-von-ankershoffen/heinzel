import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { SchemaMcpStatus } from "@/api/client";
import { OperationDialog } from "@/components/OperationDialog";
import * as browser from "@/lib/browser";
import { dismissOperation } from "@/lib/operationRun";
import { eventStream, expectAccessible, mockApi } from "@/test/render";
import { ManagedMcpSettings } from "./ManagedMcpSettings";

const empty: SchemaMcpStatus = {
  mode: "managed-http",
  state: "not-created",
  generation: null,
  endpoint: null,
  tools: null,
  ownership: "unverified",
  readiness: "unverified",
  owned_server: "unverified",
  termination: "unverified",
  descendants: "unverified",
  can_start: true,
  can_stop: false,
};
const ready: SchemaMcpStatus = {
  ...empty,
  state: "ready",
  generation: "fictional",
  endpoint: "http://127.0.0.1:8123/mcp",
  tools: 12,
  ownership: "verified",
  readiness: "authenticated-initialized",
  can_start: false,
  can_stop: true,
};
const stopped: SchemaMcpStatus = {
  ...empty,
  state: "stopped",
  generation: "fictional",
  ownership: "verified",
  owned_server: "closed",
};

afterEach(() => {
  dismissOperation();
  vi.restoreAllMocks();
});

function show(initial = empty, local = true) {
  vi.spyOn(browser, "isLocalServer").mockReturnValue(local);
  let report = initial;
  const { calls } = mockApi({
    "/api/system/mcp/check": () => report,
    "/api/stream/system/mcp/start": () => {
      report = ready;
      return eventStream([], report);
    },
    "/api/stream/system/mcp/stop": () => {
      report = stopped;
      return eventStream([], report);
    },
  });
  const view = render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ManagedMcpSettings />
      <OperationDialog />
    </QueryClientProvider>,
  );
  return { ...view, calls, user: userEvent.setup() };
}

it("uses the normal preview/cancel and typed shared operation body", async () => {
  const { calls, user, container } = show();
  const start = await screen.findByRole("button", { name: "Start managed server" });
  await user.click(start);
  const dialog = screen.getByRole("dialog");
  expect(dialog).toHaveTextContent("It will not start at login");
  expect(calls.filter((request) => request.method === "POST")).toHaveLength(0);
  await expectAccessible(container);
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(calls.filter((request) => request.method === "POST")).toHaveLength(0);
  await user.click(start);
  await user.click(screen.getByRole("button", { name: "Run it" }));
  await waitFor(() => expect(calls.filter((request) => request.method === "POST")).toHaveLength(1));
  const post = calls.find((request) => request.method === "POST");
  expect(new URL(post?.url ?? "").pathname).toBe("/api/stream/system/mcp/start");
  expect(new URL(post?.url ?? "").search).toBe("");
  expect(await post?.clone().json()).toEqual({ confirm: true });
  expect(await screen.findByText(/authenticated and initialized with 12 tools/)).toBeVisible();
});

it("allows recovery Stop even when new starts are unavailable and retains uncertainty", async () => {
  const { calls, user } = show(ready);
  await user.click(await screen.findByRole("button", { name: "Stop managed server" }));
  expect(screen.queryByRole("button", { name: "Start managed server" })).toBeNull();
  expect(screen.getByRole("dialog")).toHaveTextContent("only this profile's owned MCP server");
  expect(calls.filter((request) => request.method === "POST")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Run it" }));
  expect(await screen.findByText(/Descendant processes have not been verified/)).toBeVisible();
  expect(calls.filter((request) => request.method === "POST").map((request) => new URL(request.url).pathname)).toEqual([
    "/api/stream/system/mcp/stop",
  ]);
});

it("does not offer new starts on a disabled profile", async () => {
  show({ ...empty, state: "unavailable", can_start: false });
  expect(await screen.findByText(/preview is unavailable/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Start managed server" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop managed server" })).toBeNull();
});

it("does not make a management request from a remote browser", () => {
  const { calls } = show(ready, false);
  expect(screen.getByText(/Open Settings on this computer/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Stop managed server" })).toBeNull();
  expect(calls).toHaveLength(0);
});
