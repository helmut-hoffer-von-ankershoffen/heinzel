import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";
import { startUpdate } from "@/updates/controller";
import { preserveUpdateWork } from "@/updates/work";

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
  const icon = await screen.findByRole("button", { name: "Update available, warning" });
  expect(icon).toHaveClass("animate-pulse", "text-bad");
  await user.hover(icon);
  expect(startUpdate).not.toHaveBeenCalled();
  await user.click(icon);
  expect(startUpdate).not.toHaveBeenCalled();
  expect(await screen.findByText(/^Last installed:/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Update hz and plugins" }));
  expect(startUpdate).toHaveBeenCalledTimes(1);
});

it("shows a green update control when the installed version is current", async () => {
  mockApi({ ...shell, "/api/update-status": { ...status, latest: status.current, available: false } });
  renderAt("/");
  const icon = await screen.findByRole("button", { name: "Up to date, OK" });
  await waitFor(() => {
    expect(icon).toHaveClass("text-ok");
    expect(icon).not.toHaveClass("animate-pulse", "text-bad");
  });
  await userEvent.setup().hover(icon);
  expect(await screen.findByText("Installed: hz 0.1.0")).toBeInTheDocument();
  const expectedDate = new Date(status.installed_at).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  expect(screen.getByText(`Last installed: ${expectedDate}`)).toBeInTheDocument();
  expect(screen.queryByText("Up to date · hz 0.1.0")).not.toBeInTheDocument();
});

it("allows an explicit plugin refresh while core is current without starting it on inspection", async () => {
  mockApi({ ...shell, "/api/update-status": { ...status, latest: status.current, available: false } });
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Up to date, OK" }));
  expect(startUpdate).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Update hz and plugins" }));
  expect(startUpdate).toHaveBeenCalledTimes(1);
});

it("shows a manual-install reason instead of overriding a deliberate pin", async () => {
  mockApi({
    ...shell,
    "/api/update-status": { ...status, supported: false, reason: "Deliberate pin: update it where installed." },
  });
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Update available, warning" }));
  expect(await screen.findByText(/Deliberate pin/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Update hz and plugins" })).not.toBeInTheDocument();
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
  await user.click(await screen.findByRole("button", { name: "Update available, warning" }));
  await user.click(screen.getByRole("button", { name: "Update hz and plugins" }));
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
  await user.click(await screen.findByRole("button", { name: "Update available, warning" }));
  await user.click(screen.getByRole("button", { name: "Update hz and plugins" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Installer failed before changing hz.");
  expect(startUpdate).toHaveBeenCalledTimes(1);
});

it("does not claim to be up to date or invent an installation date when those facts are unavailable", async () => {
  mockApi({ ...shell, "/api/update-status": { ...status, latest: null, installed_at: null, available: false } });
  renderAt("/");
  const icon = await screen.findByRole("button", { name: "Updates, off" });
  await userEvent.setup().hover(icon);
  expect(await screen.findByText("Update availability is unavailable.")).toBeInTheDocument();
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
  const icon = await screen.findByRole("button", { name: "Update available, warning" });
  await user.hover(icon);
  await user.click(icon);
  expect(startUpdate).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Update hz and plugins" }));
  expect(startUpdate).toHaveBeenCalledTimes(1);
  match.mockRestore();
});
