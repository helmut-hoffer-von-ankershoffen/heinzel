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
const status = { current: "0.1.0", latest: "0.2.0", available: true, supported: true, reason: null };

it("offers a compact one-click update only after a deliberate gesture", async () => {
  mockApi({ ...shell, "/api/update-status": status });
  const user = userEvent.setup();
  renderAt("/");
  const icon = await screen.findByRole("button", { name: "Update hz and plugins, warning" });
  expect(icon).toHaveClass("animate-pulse", "text-bad");
  await user.hover(icon);
  expect(startUpdate).not.toHaveBeenCalled();
  await user.click(icon);
  expect(startUpdate).toHaveBeenCalledTimes(1);
});

it("shows a green update control when the installed version is current", async () => {
  mockApi({ ...shell, "/api/update-status": { ...status, latest: status.current, available: false } });
  renderAt("/");
  const icon = await screen.findByRole("button", { name: "Updates, off" });
  await waitFor(() => {
    expect(icon).toHaveClass("text-ok");
    expect(icon).not.toHaveClass("animate-pulse", "text-bad");
  });
});

it("shows a manual-install reason instead of overriding a deliberate pin", async () => {
  mockApi({
    ...shell,
    "/api/update-status": { ...status, supported: false, reason: "Deliberate pin: update it where installed." },
  });
  const user = userEvent.setup();
  renderAt("/");
  await user.click(await screen.findByRole("button", { name: "Updates, warning" }));
  expect(await screen.findByText(/Deliberate pin/)).toBeInTheDocument();
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
  await user.click(await screen.findByRole("button", { name: "Update hz and plugins, warning" }));
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
  await user.click(await screen.findByRole("button", { name: "Update hz and plugins, warning" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Installer failed before changing hz.");
  expect(startUpdate).toHaveBeenCalledTimes(1);
});
