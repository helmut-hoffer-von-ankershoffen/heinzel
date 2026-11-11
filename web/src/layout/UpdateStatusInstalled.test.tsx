import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";
import { loadInstalledCore, startUpdate } from "@/updates/controller";

vi.mock("@/updates/controller", async (load) => ({
  ...(await load<typeof import("@/updates/controller")>()),
  startUpdate: vi.fn(async () => "Verified"),
  loadInstalledCore: vi.fn(async () => "Heinzel 0.2.0 is running."),
}));
beforeEach(() => {
  vi.mocked(startUpdate).mockClear();
  vi.mocked(loadInstalledCore).mockClear();
  document.body.innerHTML = "";
});
// The installation holds 0.2.0 while this server still runs 0.1.0: a restart was missed.
const status = {
  current: "0.1.0",
  latest: "0.2.0",
  installed_at: null,
  available: true,
  supported: true,
  reason: null,
  installed: "0.2.0",
};

it("loads an installed but not running core by itself once, without installing", async () => {
  mockApi({ ...shell, "/api/update-status": status });
  renderAt("/");
  await waitFor(() => expect(loadInstalledCore).toHaveBeenCalledTimes(1));
  expect(vi.mocked(loadInstalledCore).mock.calls[0]?.[0]).toBe("0.2.0");
  expect(startUpdate).not.toHaveBeenCalled();
});

it("waits while work is open and offers loading it by hand instead of a refused update", async () => {
  const open = document.createElement("div");
  open.setAttribute("role", "alertdialog");
  document.body.append(open);
  mockApi({ ...shell, "/api/update-status": status });
  renderAt("/");
  const icon = await screen.findByRole("button", { name: /Updates available/ });
  await userEvent.setup().click(icon);
  expect(await screen.findByText("Heinzel 0.2.0 is installed but this app still runs 0.1.0")).toBeInTheDocument();
  expect(screen.getByText("It restarts by itself once nothing is open or running here.")).toBeInTheDocument();
  expect(loadInstalledCore).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Update now" })).not.toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", { name: "Load Heinzel 0.2.0" }));
  expect(loadInstalledCore).toHaveBeenCalledTimes(1);
  expect(startUpdate).not.toHaveBeenCalled();
});
