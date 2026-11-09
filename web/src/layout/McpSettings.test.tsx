import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";

const id = "a".repeat(32);
const handoff = `1794220000.${"b".repeat(64)}`;

it("reports the missing installed host without showing action buttons", async () => {
  mockApi({ ...shell, "/api/mcp/lifecycle": { planning_available: false } });
  renderAt("/doctor?settings=mcp-connections");
  const page = await screen.findByRole("region", { name: "MCP connections" });
  expect(await within(page).findByText(/This server has no host connection/)).toBeVisible();
  expect(within(page).queryByRole("button")).not.toBeInTheDocument();
});

it("reads authenticated observations and sends only the prepared locator to native handoff", async () => {
  const mcpHandoff = vi.fn(async () => true);
  vi.stubGlobal("heinzelDesktop", { mcpHandoff });
  try {
    const { calls } = mockApi({
      ...shell,
      "/api/mcp/lifecycle": { planning_available: true },
      "/api/mcp/lifecycle/fixture": {
        host: "observed",
        worker: "running",
        manager: "absent",
        transport: "reachable",
        mcp_readiness: "unverified",
        client: "unverified",
        actions: ["check", "stop"],
      },
      "/api/mcp/lifecycle/fixture/prepare": {
        id,
        action: "stop",
        registration: "fixture",
        session: "owning-session",
        handoff,
      },
    });
    const user = userEvent.setup();
    renderAt("/doctor?settings=mcp-connections");
    const page = await screen.findByRole("region", { name: "MCP connections" });
    await user.type(await within(page).findByLabelText("Registration ID"), "fixture");
    await user.click(within(page).getByRole("button", { name: "Read status" }));
    expect(await within(page).findByText("Reachable")).toBeVisible();
    expect(within(page).getAllByText("Unverified")).toHaveLength(2);
    await user.click(within(page).getByRole("button", { name: "Prepare Stop" }));
    const prepared = calls.find((request) => new URL(request.url).search === "?action=stop");
    expect(prepared?.method).toBe("POST");
    expect(prepared?.headers.get("Authorization")).toBe("Bearer test-token");
    expect(mcpHandoff).not.toHaveBeenCalled();
    await user.click(await within(page).findByRole("button", { name: "Open terminal handoff" }));
    expect(mcpHandoff).toHaveBeenCalledExactlyOnceWith(id);
    expect(await within(page).findByText("Command copied for local review.")).toBeVisible();
    expect(within(page).queryByText("Stopped")).not.toBeInTheDocument();
    expect(calls.filter((request) => request.method === "POST" && request.url.includes("/api/mcp/"))).toHaveLength(1);
  } finally {
    vi.unstubAllGlobals();
  }
});

it("leaves the optional browser command behind an explicit disclosure and describes Install as review only", async () => {
  mockApi({
    ...shell,
    "/api/mcp/lifecycle": { planning_available: true },
    "/api/mcp/lifecycle/fixture": {
      host: "observed",
      worker: "unobserved",
      manager: "not-checked",
      transport: "not-checked",
      mcp_readiness: "unverified",
      client: "unverified",
      actions: ["install"],
    },
    "/api/mcp/lifecycle/fixture/prepare": {
      id,
      action: "install",
      registration: "fixture",
      session: "terminal",
      handoff,
    },
  });
  const user = userEvent.setup();
  renderAt("/doctor?settings=mcp-connections");
  const page = await screen.findByRole("region", { name: "MCP connections" });
  await user.type(await within(page).findByLabelText("Registration ID"), "fixture");
  await user.click(within(page).getByRole("button", { name: "Read status" }));
  await user.click(await within(page).findByRole("button", { name: "Prepare Install" }));
  expect(await within(page).findByText(/leaves the client configuration untouched/)).toBeVisible();
  const disclosure = within(page).getByText("Show command for local review");
  expect(disclosure.closest("details")).not.toHaveAttribute("open");
  await user.click(disclosure);
  expect(disclosure.closest("details")).toHaveAttribute("open");
  expect(within(page).getByText(`hz system mcp review-install fixture --handoff ${handoff}`)).toBeVisible();
});

it.each([false, true])("keeps every client connection unverified with host availability %s", async (planning) => {
  const { calls } = mockApi({ ...shell, "/api/mcp/lifecycle": { planning_available: planning } });
  renderAt("/doctor?settings=mcp-connections");
  const clients = await screen.findByRole("region", { name: "Client connections" });
  for (const name of ["Claude Code", "Codex", "Cursor"]) {
    expect(within(clients).getByText(`${name}: Connection not checked`)).toBeVisible();
  }
  expect(calls.filter((request) => request.method !== "GET")).toHaveLength(0);
});

it("withdraws stale actions during refresh and after failure, then reports no offered actions", async () => {
  let response: unknown = {
    host: "observed",
    worker: "running",
    manager: "absent",
    transport: "reachable",
    mcp_readiness: "unverified",
    client: "unverified",
    actions: ["stop"],
  };
  const { calls } = mockApi({
    ...shell,
    "/api/mcp/lifecycle": { planning_available: true },
    "/api/mcp/lifecycle/fixture": () => response,
  });
  const user = userEvent.setup();
  renderAt("/doctor?settings=mcp-connections");
  const page = await screen.findByRole("region", { name: "MCP connections" });
  await user.type(await within(page).findByLabelText("Registration ID"), "fixture");
  await user.click(within(page).getByRole("button", { name: "Read status" }));
  expect(await within(page).findByRole("button", { name: "Prepare Stop" })).toBeVisible();
  expect(within(page).getByText("Claude Code: Connection not checked")).toBeVisible();
  let finish!: (value: unknown) => void;
  response = new Promise((resolve) => {
    finish = resolve;
  });
  await user.click(within(page).getByRole("button", { name: "Read status" }));
  expect(within(page).queryByRole("button", { name: "Prepare Stop" })).not.toBeInTheDocument();
  finish({ status: 503, body: { error: "unavailable" } });
  expect(await within(page).findByText("Could not read MCP status.")).toBeVisible();
  expect(within(page).queryByRole("button", { name: "Prepare Stop" })).not.toBeInTheDocument();
  response = {
    host: "observed",
    worker: "running",
    manager: "absent",
    transport: "reachable",
    mcp_readiness: "unverified",
    client: "unverified",
    actions: [],
  };
  await user.click(within(page).getByRole("button", { name: "Read status" }));
  expect(await within(page).findByText("The host offers no lifecycle actions for this registration.")).toBeVisible();
  expect(calls.filter((request) => request.method !== "GET")).toHaveLength(0);
});
