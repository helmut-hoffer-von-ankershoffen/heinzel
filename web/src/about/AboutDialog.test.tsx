import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AboutDialog, daemonText, detailsText } from "./AboutDialog";
import { requestMenuAction } from "./menu";

const info = {
  version: "1.2.3",
  latest_release: null,
  plugins: [
    { name: "heinzel-a", repository: null },
    { name: "heinzel-b", repository: "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-b" },
  ],
};
const report = {
  version: "1.2.3",
  installed: "2026-11-08",
  installation: "uv tool",
  source: "PyPI",
  channel: "stable",
  web_build: "0123456789ab",
  desktop: null,
  daemon: { state: "running", version: "1.2.3", outdated: false },
  python_version: "3.14.1",
  platform: "macOS",
  plugins: [
    { name: "heinzel-a", version: "0.4.0", source: "PyPI", pinned: null, installed: "2026-11-01" },
    { name: "heinzel-b", version: "0.4.0", source: "git", pinned: "v0.4.0", installed: null },
  ],
  diagnostics: ["hz 1.2.3 (installed 2026-11-08)", "plugin heinzel-a 0.4.0 from PyPI, follows updates"],
};

const REPOSITORY = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel";
let current: unknown = report;
let failed = false;
const refetch = vi.fn();
vi.mock("@/api/queries", () => ({
  useInfo: () => ({ data: info, isError: false }),
  useAbout: () => ({ data: current, isError: failed, refetch }),
}));

const show = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <StrictMode>
        <AboutDialog />
      </StrictMode>
    </QueryClientProvider>,
  );

afterEach(() => {
  vi.restoreAllMocks();
  current = report;
  failed = false;
});

describe("About dialog", () => {
  it("only exposes native About details to the owned desktop frame", () => {
    // The focused web lane owns the About surface and can run this dependency-free IPC suite with its pinned Node.
    const suite = resolve(dirname(fileURLToPath(import.meta.url)), "../../../desktop/test/lifecycle-ipc.test.js");
    execFileSync(process.execPath, ["--test", suite], { encoding: "utf8", timeout: 3_000 });
  });

  it("says so when no plugin is installed", () => {
    current = { ...report, plugins: [] };
    show();
    act(() => requestMenuAction("about"));
    expect(screen.getByText("No plugins installed.")).toBeInTheDocument();
  });

  it("shows one row per plugin, many or long-named, in cells that break instead of overflowing", () => {
    const long = `heinzel-${"x".repeat(200)}`;
    current = {
      ...report,
      plugins: [
        { name: long, version: "1.0.0", source: "path", pinned: "local folder", installed: null },
        ...Array.from({ length: 40 }, (_, i) => ({
          name: `heinzel-p${i}`,
          version: "0.1.0",
          source: "PyPI",
          pinned: null,
          installed: null,
        })),
      ],
    };
    show();
    act(() => requestMenuAction("about"));
    expect(screen.getAllByRole("row")).toHaveLength(42);
    expect(screen.getByRole("cell", { name: long })).toHaveClass("break-all");
  });

  it("keeps the header mascot inside its head-sized slot", () => {
    show();
    act(() => requestMenuAction("about"));
    const dialog = screen.getByRole("dialog", { name: "Heinzelmännchen" });
    const mascot = within(dialog).getByRole("img", { name: "Heinzelmännchen" });
    expect(mascot).toHaveAttribute("data-view", "head");
    expect(mascot).toHaveClass("size-12", "overflow-hidden");
    expect(mascot).not.toHaveClass("overflow-visible");
  });

  it("copies hz system about's lines, then this page's build when it differs and the desktop app it runs in", () => {
    const text = detailsText(report as never, "fedcba987654", { version: "0.9.29", path: "/Applications/H.app" });
    expect(text.split("\n")).toEqual([
      ...report.diagnostics,
      "web build of this page fedcba987654",
      "desktop app running 0.9.29 /Applications/H.app",
    ]);
    expect(detailsText(report as never, "0123456789ab", null)).toBe(report.diagnostics.join("\n"));
    expect(detailsText({ ...report, web_build: null } as never, null, null)).toBe(report.diagnostics.join("\n"));
    expect(detailsText(report as never, null, null)).toContain("web build of this page development");
  });

  it("names each daemon state, with the hz it runs when known", () => {
    expect(daemonText({ state: "running", version: "1.2.3", outdated: false })).toBe("Running, hz 1.2.3");
    expect(daemonText({ state: "paused", version: null, outdated: false })).toBe("Paused");
    expect(daemonText({ state: "stopped", version: null, outdated: false })).toBe("Stopped");
    expect(daemonText({ state: "not installed", version: null, outdated: false })).toBe("Not installed");
    expect(daemonText({ state: "unknown", version: null, outdated: false })).toBe("Unknown");
  });

  it("shows the install source, channel, daemon, desktop app and each plugin's pin, and asks again on open", () => {
    current = { ...report, desktop: "0.9.30", daemon: { state: "running", version: "1.2.2", outdated: true } };
    show();
    act(() => requestMenuAction("about"));
    const dialog = screen.getByRole("dialog", { name: "Heinzelmännchen" });
    expect(refetch).toHaveBeenCalled();
    expect(within(dialog).getByText("uv tool from PyPI")).toBeInTheDocument();
    expect(within(dialog).getByText("stable")).toBeInTheDocument();
    expect(within(dialog).getByText("Running, hz 1.2.2")).toBeInTheDocument();
    expect(within(dialog).getByText("Restarts on the new hz with its next tick.")).toBeInTheDocument();
    expect(within(dialog).getByText("0.9.30, installed on this Mac")).toBeInTheDocument();
    expect(within(dialog).getByText("Pinned to v0.4.0")).toBeInTheDocument();
    expect(within(dialog).getByText("Follows updates")).toBeInTheDocument();
    // No hashed entrypoints in a test page: the page runs a development build.
    expect(within(dialog).getByText("Development")).toBeInTheDocument();
  });

  it("says when no desktop app is installed and opens only once", () => {
    show();
    act(() => requestMenuAction("about"));
    expect(screen.getByText("Not installed")).toBeInTheDocument();
    act(() => requestMenuAction("about")); // already open: nothing new
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("says the details could not be loaded when the report fails", () => {
    current = undefined;
    failed = true;
    show();
    act(() => requestMenuAction("about"));
    expect(screen.getByText("The details could not be loaded.")).toBeInTheDocument();
  });

  it("opens on the about menu action, shows the plugin table and copies the details", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    show();
    expect(screen.queryByRole("dialog")).toBeNull();
    act(() => requestMenuAction("about"));
    const dialog = screen.getByRole("dialog", { name: "Heinzelmännchen" });
    expect(dialog).toHaveAttribute("data-hz-about-dialog", "");
    expect(within(dialog).getByRole("link", { name: /release/i })).toHaveAttribute(
      "href",
      `${REPOSITORY}/releases/tag/v1.2.3`,
    );
    expect(within(dialog).getByRole("link", { name: "Source repository" })).toHaveAttribute("href", REPOSITORY);
    expect(screen.getByRole("cell", { name: "heinzel-b" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /copy diagnostics/i }));
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining("plugin heinzel-a 0.4.0 from PyPI, follows updates"),
    );
    act(() => requestMenuAction("copy")); // the desktop menu's Copy Diagnostics copies the same
    expect(writeText).toHaveBeenLastCalledWith(writeText.mock.calls[0]?.[0]);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("contains forward and backward keyboard focus and restores its opener on Escape", async () => {
    const user = userEvent.setup();
    const opener = document.createElement("button");
    opener.textContent = "Open About";
    document.body.prepend(opener);
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");
    try {
      show();
      opener.focus();
      act(() => requestMenuAction("about"));
      const dialog = screen.getByRole("dialog", { name: "Heinzelmännchen" });
      expect(showModal).toHaveBeenCalledOnce();
      expect(dialog).toHaveAttribute("open");
      const close = within(dialog).getByRole("button", { name: "Close" });
      expect(close).toHaveFocus();
      await user.tab();
      expect(within(dialog).getByRole("button", { name: "Copy diagnostics" })).toHaveFocus();
      await user.tab({ shift: true });
      expect(close).toHaveFocus();
      await user.tab({ shift: true });
      expect(within(dialog).getByRole("link", { name: "Source repository" })).toHaveFocus();
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(opener).toHaveFocus();
    } finally {
      opener.remove();
    }
  });

  it("skips unavailable controls and restores focus after native cancellation", async () => {
    const user = userEvent.setup();
    current = null;
    const opener = document.createElement("button");
    document.body.prepend(opener);
    try {
      show();
      opener.focus();
      act(() => requestMenuAction("about"));
      const dialog = screen.getByRole("dialog");
      expect(within(dialog).getByRole("button", { name: "Copy diagnostics" })).toBeDisabled();
      await user.tab();
      expect(within(dialog).getByRole("link", { name: "Source repository" })).toHaveFocus();
      fireEvent(dialog, new Event("cancel", { bubbles: false, cancelable: true }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(opener).toHaveFocus();
    } finally {
      opener.remove();
    }
  });

  it("restores connected focus on unmount and leaves existing background inertness intact", () => {
    const opener = document.createElement("button");
    const background = document.createElement("section");
    background.setAttribute("inert", "");
    document.body.prepend(opener, background);
    try {
      const mounted = show();
      opener.focus();
      act(() => requestMenuAction("about"));
      expect(background).toHaveAttribute("inert");
      mounted.unmount();
      expect(opener).toHaveFocus();
      expect(background).toHaveAttribute("inert");
    } finally {
      opener.remove();
      background.remove();
    }
  });

  it("shows versions with their dates, a first-party chip, and nothing for an unknown date", () => {
    show();
    act(() => requestMenuAction("about"));
    expect(screen.getByText(/Installed Nov 8, 2026/)).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(within(rows[1] as HTMLElement).getByText("Nov 1, 2026")).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).getByText("first-party")).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).queryByText(/unknown|Nov/)).toBeNull();
  });
});
