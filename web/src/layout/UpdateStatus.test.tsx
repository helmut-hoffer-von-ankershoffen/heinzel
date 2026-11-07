import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";
import { KEY } from "@/updates/autoUpdateApi";
import { startUpdate } from "@/updates/controller";
import { preserveUpdateWork } from "@/updates/work";
import { UpdateStatus } from "./UpdateStatus";

vi.mock("@/updates/controller", async (load) => ({
  ...(await load<typeof import("@/updates/controller")>()),
  startUpdate: vi.fn(async () => "Verified"),
}));
beforeEach(() => vi.mocked(startUpdate).mockClear());
const status = {
  current: "0.1.0",
  latest: "0.2.0",
  installed_at: "2026-01-02T12:00:00Z",
  available: true,
  supported: true,
  reason: null,
};

it("opens the update details without installing until the button is chosen", async () => {
  mockApi({ ...shell, "/api/update-status": status });
  const user = userEvent.setup();
  renderAt("/");
  const icon = await screen.findByRole("button", { name: "Updates available, warning" });
  expect(icon).toHaveClass("animate-pulse", "text-bad");
  await user.hover(icon);
  expect(startUpdate).not.toHaveBeenCalled();
  await user.click(icon);
  expect(startUpdate).not.toHaveBeenCalled();
  expect(await screen.findByText(/^Last installed:/)).toBeInTheDocument();
  expect(screen.getByText("Heinzel 0.2.0 is available")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Update now" }));
  expect(startUpdate).toHaveBeenCalledTimes(1);
});

it("shows inspection guidance without an unusable verify action after a lost acknowledgement", async () => {
  mockApi({ ...shell, "/api/update-status": status });
  sessionStorage.setItem("hz.update.receipt", JSON.stringify({ stage: "uncertain" }));
  try {
    renderAt("/");
    const icon = await screen.findByRole("button", { name: /Updates available.*error/i });
    await userEvent.setup().click(icon);
    expect(await screen.findByRole("alert")).toHaveTextContent("Run hz system info");
    expect(screen.queryByRole("button", { name: "Verify update again" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Update now" })).not.toBeInTheDocument();
    expect(startUpdate).not.toHaveBeenCalled();
  } finally {
    sessionStorage.removeItem("hz.update.receipt");
  }
});

it("keeps a current core neutral because an empty plan cannot establish every plugin is current", async () => {
  mockApi({ ...shell, "/api/update-status": { ...status, latest: status.current, available: false } });
  renderAt("/");
  const icon = await screen.findByRole("button", { name: "Updates, off" });
  await waitFor(() => {
    expect(icon).not.toHaveClass("text-ok");
    expect(icon).not.toHaveClass("animate-pulse", "text-bad");
  });
  await userEvent.setup().hover(icon);
  expect(await screen.findByText("Couldn't check plugin updates right now")).toBeInTheDocument();
  const expectedDate = new Date(status.installed_at).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  expect(screen.getByText(`Last installed: ${expectedDate}`)).toBeInTheDocument();
  expect(screen.queryByText(/is up to date/)).not.toBeInTheDocument();
});

it("labels the explicit full updater as manual when no cached update is known", async () => {
  mockApi({ ...shell, "/api/update-status": { ...status, latest: status.current, available: false } });
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Updates, off" }));
  expect(startUpdate).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Manually update…" }));
  expect(startUpdate).toHaveBeenCalledTimes(1);
});

it("shows a manual-install reason instead of overriding a deliberate pin", async () => {
  mockApi({
    ...shell,
    "/api/update-status": { ...status, supported: false, reason: "Deliberate pin: update it where installed." },
  });
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Updates available, warning" }));
  expect(await screen.findByText(/Deliberate pin/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Update now" })).not.toBeInTheDocument();
  expect(startUpdate).not.toHaveBeenCalled();
});

it("does not start by itself and keeps the mounted Composer draft across the update reload", async () => {
  mockApi({ ...shell, "/api/update-status": status });
  const user = userEvent.setup();
  const first = renderAt("/");
  const draft = await screen.findByPlaceholderText("Ask about this screen, a contract, a command…");
  await user.type(draft, "Keep this draft through the update");
  expect(startUpdate).not.toHaveBeenCalled();

  vi.mocked(startUpdate).mockImplementationOnce(async () => {
    preserveUpdateWork();
    return "Reloading";
  });
  await user.click(await screen.findByRole("button", { name: "Updates available, warning" }));
  await user.click(screen.getByRole("button", { name: "Update now" }));
  expect(sessionStorage.getItem("hz.update.composer")).toContain("Keep this draft through the update");
  first.unmount();

  renderAt("/");
  expect(await screen.findByDisplayValue("Keep this draft through the update")).toBeInTheDocument();
});

it("reports an installer failure and never retries installation without another click", async () => {
  mockApi({ ...shell, "/api/update-status": status });
  vi.mocked(startUpdate).mockRejectedValueOnce(new Error("Installer failed before changing hz."));
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Updates available, warning" }));
  await user.click(screen.getByRole("button", { name: "Update now" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Installer failed before changing hz.");
  expect(startUpdate).toHaveBeenCalledTimes(1);
});

it("does not claim to be up to date or invent an installation date when those facts are unavailable", async () => {
  mockApi({ ...shell, "/api/update-status": { ...status, latest: null, installed_at: null, available: false } });
  renderAt("/");
  const icon = await screen.findByRole("button", { name: "Updates, off" });
  await userEvent.setup().hover(icon);
  expect(await screen.findByText("Checking for updates…")).toBeInTheDocument();
  expect(screen.getByText("Installation date unavailable.")).toBeInTheDocument();
  expect(screen.queryByText("Up to date")).not.toBeInTheDocument();
});

it("does not call a deliberately pinned install up to date", async () => {
  mockApi({
    ...shell,
    "/api/update-status": {
      ...status,
      latest: status.current,
      available: false,
      supported: false,
      reason: "Deliberate pin.",
    },
  });
  renderAt("/");
  await userEvent.setup().click(await screen.findByRole("button", { name: "Updates, off" }));
  expect(screen.getByText("Deliberate pin.")).toBeInTheDocument();
  expect(screen.queryByText("Up to date")).not.toBeInTheDocument();
});

it("opens details before updating with a fine pointer too", async () => {
  const original = window.matchMedia;
  const match = vi.spyOn(window, "matchMedia").mockImplementation((query) => ({ ...original(query), matches: true }));
  mockApi({ ...shell, "/api/update-status": status });
  const user = userEvent.setup();
  renderAt("/");
  const icon = await screen.findByRole("button", { name: "Updates available, warning" });
  await user.hover(icon);
  await user.click(icon);
  expect(startUpdate).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Update now" }));
  expect(startUpdate).toHaveBeenCalledTimes(1);
  match.mockRestore();
});

const autoUpdate = {
  section: "hz",
  key: "auto_update",
  value: true,
  default: true,
  source: "default",
  env: "HZ_AUTO_UPDATE",
  description: "Installs new versions of hz and its plugins by itself.",
  json_schema: { type: "boolean", title: "Automatic updates", default: true, "x-live": true },
};
const config = { files: [], settings: [autoUpdate], plugins: [] };

it("has the Automatic updates switch, on by default, which saves the same setting as Settings", async () => {
  const { calls } = mockApi({
    ...shell,
    "/api/update-status": status,
    "/api/system/config/show": config,
    "/api/system/config/set": "/home/mira/.config/hz/hz.toml",
    "/api/auto-update": { phase: "idle", development: null },
  });
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Updates available, warning" }));
  const toggle = await screen.findByRole("switch", { name: "Automatic updates" });
  await waitFor(() => expect(toggle).toBeEnabled());
  expect(toggle).toHaveAttribute("aria-checked", "true");
  expect(screen.queryByText(/after a 30-second warning you can postpone/)).not.toBeInTheDocument();
  await user.click(toggle);
  await waitFor(() =>
    expect(calls.find((call) => call.url.includes("/system/config/set"))?.url).toContain("key=hz.auto_update"),
  );
});

it("disables the footer switch and says why while hz runs from a source checkout", async () => {
  mockApi({
    ...shell,
    "/api/update-status": status,
    "/api/system/config/show": config,
    "/api/auto-update": { phase: "off", development: "Updates are managed by your source checkout." },
  });
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Updates available, warning" }));
  const toggle = await screen.findByRole("switch", { name: "Automatic updates" });
  await waitFor(() => expect(toggle).toBeDisabled());
  expect(toggle).toHaveAttribute("aria-checked", "false");
  expect(screen.getByText("Updates are managed by your source checkout.")).toBeInTheDocument();
});

it("disables the footer switch and says so while update checks are off", async () => {
  const line = "Update checks are off, so hz neither looks for updates nor installs them by itself.";
  mockApi({
    ...shell,
    "/api/update-status": status,
    "/api/system/config/show": config,
    "/api/auto-update": { phase: "off", development: null, held: line },
  });
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Updates available, warning" }));
  const toggle = await screen.findByRole("switch", { name: "Automatic updates" });
  await waitFor(() => expect(toggle).toBeDisabled());
  expect(toggle).toHaveAttribute("aria-checked", "false");
  expect(screen.getByText(line)).toBeInTheDocument();
});

it("names the plugins from git or a folder that are updated by hand", async () => {
  mockApi({
    ...shell,
    "/api/update-status": status,
    "/api/system/config/show": config,
    "/api/auto-update": {
      phase: "idle",
      development: null,
      held: null,
      by_hand: ["heinzel-crm"],
      holds: [{ name: "heinzel-crm", why: "source", how: "git" }],
    },
  });
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Updates available, warning" }));
  expect(
    await screen.findByText(
      "heinzel-crm is installed from the Heinzel catalog source and isn't followed for updates yet",
    ),
  ).toBeInTheDocument();
});

const plan = {
  enabled: true,
  reason: null,
  phase: "idle",
  version: "0.1.0",
  core: null,
  plugins: {},
  seconds_left: null,
  apply_at: null,
  next_window_at: null,
  postponements: 0,
  message: null,
};

it.each([
  ["plugin only", false, { "heinzel-example": "2.0.0" }, "heinzel-example 2.0.0 is available"],
  ["core only", true, {}, "Heinzel 0.2.0 is available"],
  ["core and plugin", true, { "heinzel-example": "2.0.0" }, "heinzel-example 2.0.0 is available"],
] as const)("shows %s cached news without mutating on inspection", async (_name, core, plugins, line) => {
  const { calls } = mockApi({
    ...shell,
    "/api/update-status": { ...status, available: core, latest: core ? status.latest : status.current },
    "/api/auto-update": { ...plan, plugins },
  });
  renderAt("/");
  await userEvent.setup().click(await screen.findByRole("button", { name: "Updates available, warning" }));
  expect(await screen.findByText(line)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: core && Object.keys(plugins).length ? "Update all" : "Update now" }),
  ).toBeInTheDocument();
  expect(startUpdate).not.toHaveBeenCalled();
  expect(
    calls
      .filter((call) => /\/(auto-update|update-status)$/.test(new URL(call.url).pathname))
      .every((call) => call.method === "GET"),
  ).toBe(true);
});

it.each([plan, { ...plan, phase: "off", enabled: false }, { status: 503, body: {} }])(
  "does not call an empty, disabled or failed plan current",
  async (answer) => {
    mockApi({
      ...shell,
      "/api/update-status": { ...status, latest: status.current, available: false },
      "/api/auto-update": answer,
    });
    renderAt("/");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Updates, off" }));
    expect(screen.queryByText(/up to date/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Update now" })).not.toBeInTheDocument();
    const manual = screen.getByRole("button", { name: "Manually update…" });
    // Switches stay in the body; the action sits in the bottom row under the divider (design/README.md).
    expect(manual.closest("[data-pane-actions]")).toHaveClass("border-t");
    for (const toggle of screen.queryAllByRole("switch")) expect(toggle.closest("[data-pane-actions]")).toBeNull();
    expect(startUpdate).not.toHaveBeenCalled();
  },
);

it("observes the live plan instead of the indefinitely cached installation facts", async () => {
  mockApi({
    ...shell,
    "/api/update-status": { ...status, latest: status.current, available: false },
    "/api/auto-update": plan,
  });
  const client = queryClient();
  client.setQueryData([...KEY, "held", null], { ...plan, plugins: { "heinzel-old": "9.0.0" } });
  client.setQueryData(KEY, plan);
  render(
    <Providers client={client}>
      <UpdateStatus />
    </Providers>,
  );
  await userEvent.setup().click(await screen.findByRole("button", { name: "Updates, off" }));
  expect(screen.queryByText(/heinzel-old/)).not.toBeInTheDocument();
  await act(async () => {
    client.setQueryData(KEY, { ...plan, plugins: { "heinzel-example": "2.0.0" } });
  });
  expect(await screen.findByText("heinzel-example 2.0.0 is available")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Update now" })).toBeInTheDocument();
  expect(startUpdate).not.toHaveBeenCalled();
});
