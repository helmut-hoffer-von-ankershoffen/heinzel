import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import type { SchemaDaemonStatus } from "@/api/client";
import { config, shell } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { DaemonSettings } from "./DaemonSettings";
import { SettingsDialog } from "./SettingsDialog";

const running: SchemaDaemonStatus = {
  platform: "darwin",
  installed: true,
  loaded: true,
  paused: false,
  version: "1.2.3",
  installed_version: "1.2.3",
  version_mismatch: false,
  jobs: 3,
  failing: [],
  interval_minutes: 5,
  executable: "/home/mira/.local/bin/hz",
  last_tick: null,
};

function show(initial: Partial<SchemaDaemonStatus> = {}, failed = false) {
  let status = { ...running, ...initial };
  const { calls } = mockApi({
    "/api/system/daemon/status": () => status,
    "/api/system/daemon/install": () => {
      status = { ...status, installed: true, loaded: true, version: "1.2.3" };
      return {
        platform: "darwin",
        executable: "/usr/bin/hz",
        interval_minutes: 5,
        files: {},
        commands: [],
        dry_run: false,
      };
    },
    "/api/system/daemon/uninstall": () => {
      status = { ...status, installed: false, loaded: false, version: null };
      return { removed: [], commands: [] };
    },
    "/api/system/daemon/pause": () => {
      if (failed) return { status: 400, body: { error: "Pause refused fixture" } };
      status = { ...status, paused: true };
      return { paused: true };
    },
    "/api/system/daemon/resume": () => {
      status = { ...status, paused: false };
      return { paused: false };
    },
    "/api/system/daemon/restart": () => {
      status = { ...status, loaded: true, installed_version: "2.0.0", version_mismatch: false };
      return { restarted: true, version: "2.0.0", interval_minutes: 5 };
    },
  });
  const view = render(
    <Providers client={queryClient()}>
      <DaemonSettings />
    </Providers>,
  );
  return { ...view, calls, user: userEvent.setup() };
}

beforeEach(() => {
  sessionStorage.setItem("hz.token", "test-token");
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Daemon Settings", () => {
  it.each([true, false])("mounts actual status and safe controls in Settings, installed=%s", async (installed) => {
    const { calls } = mockApi({
      ...shell,
      "/api/system/daemon/status": {
        ...running,
        installed,
        loaded: installed,
        installed_version: installed ? running.installed_version : null,
      },
      "/api/system/daemon/install": {
        platform: "darwin",
        executable: "/usr/bin/hz",
        interval_minutes: 5,
        files: {},
        commands: [],
        dry_run: false,
      },
      "/api/system/config/show": {
        ...config,
        settings: [
          {
            section: "system.daemon",
            key: "unit_dir",
            value: "/private/services",
            default: "/private/services",
            source: "default",
            env: "HZ_SYSTEM_DAEMON__UNIT_DIR",
            description: "Service configuration",
            json_schema: { type: "string", title: "Service folder", "x-expert": true },
          },
        ],
      },
      "/api/system/expert": { expert: false },
    });
    render(
      <Providers client={queryClient()}>
        <SettingsDialog section="daemon" initialQuery="" onSection={vi.fn()} onClose={vi.fn()} />
      </Providers>,
    );
    const controls = await screen.findByRole("region", { name: "Daemon status and controls" });
    expect(await within(controls).findAllByText("1.2.3")).toHaveLength(installed ? 2 : 1);
    expect(within(controls).getByText("3")).toBeInTheDocument();
    expect(screen.queryByLabelText("Service folder")).not.toBeInTheDocument();
    expect(screen.queryByText("No settings here")).not.toBeInTheDocument();
    if (installed) expect(within(controls).getByRole("button", { name: "Pause" })).toBeInTheDocument();
    else {
      const user = userEvent.setup();
      await user.click(within(controls).getByRole("button", { name: "Install" }));
      expect(within(screen.getByRole("alertdialog")).getByText(/starts automatically/)).toBeInTheDocument();
      await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Install" }));
      await waitFor(() =>
        expect(calls.some((request) => new URL(request.url).pathname.endsWith("/install"))).toBe(true),
      );
    }
  });

  it("shows actual daemon version, job count and last tick without exposing service-folder configuration", async () => {
    const lastTick = "2026-10-27T08:00:00.000Z";
    const { container, calls } = show({ last_tick: lastTick });
    expect(await screen.findAllByText("1.2.3")).toHaveLength(2);
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Last tick")).toBeInTheDocument();
    expect(screen.getByText(new Date(lastTick).toLocaleString())).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Running");
    expect(screen.queryByText("Service folder")).not.toBeInTheDocument();
    expect(calls.every((request) => request.method === "GET")).toBe(true);
    await expectAccessible(container);
  });

  it("confirms Pause and Resume and reconciles the status without replay or reload", async () => {
    const { calls, user } = show();
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    const paused = screen.getByRole("alertdialog", { name: "Pause the daemon?" });
    expect(within(paused).getByText("hz system daemon pause")).toBeInTheDocument();
    expect(within(paused).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(calls.filter((request) => request.method === "POST")).toHaveLength(0);
    await user.click(within(paused).getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Paused"));
    const resume = await screen.findByRole("button", { name: "Resume" });
    await waitFor(() => expect(resume).toBeEnabled());
    await user.click(resume);
    await user.click(
      within(screen.getByRole("alertdialog", { name: "Resume the daemon?" })).getByRole("button", { name: "Resume" }),
    );
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Running"));
    expect(
      calls.filter((request) => request.method === "POST").map((request) => new URL(request.url).pathname),
    ).toEqual(["/api/system/daemon/pause", "/api/system/daemon/resume"]);
  });

  it.each(["install", "uninstall"] as const)(
    "runs confirmed %s in the web API and refreshes daemon status",
    async (action) => {
      const { user, calls } = show(
        action === "install" ? { installed: false, loaded: false, version: null, jobs: 0 } : {},
      );
      const label = action === "install" ? "Install" : "Uninstall";
      await user.click(await screen.findByRole("button", { name: label }));
      const dialog = screen.getByRole("alertdialog", { name: `${label} the daemon?` });
      expect(dialog).toHaveTextContent(`hz system daemon ${action}`);
      expect(dialog).toHaveTextContent("starts automatically");
      expect(calls.filter((request) => request.method === "POST")).toHaveLength(0);
      await user.click(within(dialog).getByRole("button", { name: label }));
      await waitFor(() => expect(calls.filter((request) => request.method === "POST")).toHaveLength(1));
      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent(action === "install" ? "Running" : "Not installed"),
      );
      const postRequest = calls.find((request) => request.method === "POST");
      expect(postRequest).toBeDefined();
      if (postRequest) expect(new URL(postRequest.url).pathname).toBe(`/api/system/daemon/${action}`);
      expect(
        calls.filter((request) => request.method === "GET" && new URL(request.url).pathname.endsWith("/status")).length,
      ).toBeGreaterThan(1);
    },
  );

  it("Escape cancels the confirmation without running or closing the host Settings dialog", async () => {
    const { user, calls } = show();
    await user.click(await screen.findByRole("button", { name: "Uninstall" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(calls.every((request) => request.method === "GET")).toBe(true);
  });

  it("shows unknown recorded version and zero jobs honestly, and confirms a needed restart", async () => {
    const { user, calls } = show({ loaded: false, version: null, installed_version: null, jobs: 0 });
    expect(await screen.findAllByText("Unknown")).toHaveLength(2);
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("not loaded");
    await user.click(screen.getByRole("button", { name: "Restart" }));
    expect(calls.every((request) => request.method === "GET")).toBe(true);
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Restart" }));
    expect(await screen.findByText("2.0.0")).toBeInTheDocument();
    expect(calls.filter((request) => new URL(request.url).pathname.endsWith("/restart"))).toHaveLength(1);
  });

  it("explains a daemon version mismatch and offers the confirmed restart", async () => {
    const { user } = show({ version: "1.0.0", installed_version: "2.0.0", version_mismatch: true });
    expect(
      await screen.findByText("Restart the daemon and wait for a tick from the installed hz version."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Restart" }));
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
  });

  it("keeps state and reports a refused pause instead of claiming success or retrying", async () => {
    const { calls, user } = show({}, true);
    await user.click(await screen.findByRole("button", { name: "Pause" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Pause" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pause refused fixture");
    expect(screen.getByRole("status")).toHaveTextContent("Running");
    expect(calls.filter((request) => request.method === "POST")).toHaveLength(1);
  });

  it("offers no install action on unsupported platforms", async () => {
    show({ platform: "unsupported", installed: false, loaded: false });
    expect(await screen.findByText(/needs launchd or systemd/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install" })).not.toBeInTheDocument();
  });

  it("reports failing jobs as needing attention instead of claiming healthy operation", async () => {
    show({ failing: ["reports:daily"] });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Needs attention"));
  });
});
