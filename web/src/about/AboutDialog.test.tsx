import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AboutDialog, detailsText } from "./AboutDialog";
import { requestMenuAction } from "./menu";

const info = {
  version: "1.2.3",
  installed: "2026-11-08",
  repository: "https://github.com/helmut-hoffer-von-ankershoffen/heinzel",
  python_version: "3.14.1",
  python_executable: "/py",
  platform: "macOS",
  installation: "uv tool",
  latest_release: null,
  plugins: [
    { name: "heinzel-a", version: "0.4.0", source: "PyPI", repository: null, installed: "2026-11-01" },
    {
      name: "heinzel-b",
      version: "0.4.0",
      source: "git",
      repository: "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-b",
      installed: null,
    },
  ],
};

let current: unknown = info;
vi.mock("@/api/queries", () => ({ useInfo: () => ({ data: current, isError: false }) }));

const show = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AboutDialog />
    </QueryClientProvider>,
  );

afterEach(() => {
  vi.restoreAllMocks();
  current = info;
});

describe("About dialog", () => {
  it("says so when no plugin is installed", () => {
    current = { ...info, plugins: [] };
    show();
    act(() => requestMenuAction("about"));
    expect(screen.getByText("No plugins installed.")).toBeInTheDocument();
  });

  it("shows one row per plugin, many or long-named, in cells that break instead of overflowing", () => {
    const long = `heinzel-${"x".repeat(200)}`;
    current = {
      ...info,
      plugins: [
        { name: long, version: "1.0.0", source: "path", repository: null },
        ...Array.from({ length: 40 }, (_, i) => ({
          name: `heinzel-p${i}`,
          version: "0.1.0",
          source: "PyPI",
          repository: null,
        })),
      ],
    };
    show();
    act(() => requestMenuAction("about"));
    expect(screen.getAllByRole("row")).toHaveLength(42);
    expect(screen.getByRole("cell", { name: long })).toHaveClass("break-all");
  });

  it("gives the hz --version lines plus daemon, and the desktop app when there is one", () => {
    const text = detailsText(info as never, { version: "0.9.29", path: "/Applications/H.app" });
    expect(text).toContain("hz 1.2.3\n");
    expect(text).toContain("installation uv tool (installed 2026-11-08)");
    expect(text).toContain("daemon running 1.2.3");
    expect(text).toContain("desktop 0.9.29 /Applications/H.app");
    expect(text).toContain("plugin heinzel-b 0.4.0 git");
  });

  it("opens on the about menu action, shows the plugin table and copies the details", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    show();
    expect(screen.queryByRole("dialog")).toBeNull();
    act(() => requestMenuAction("about"));
    expect(screen.getByRole("dialog", { name: "Heinzelmännchen" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "heinzel-b" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /copy details/i }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("plugin heinzel-a 0.4.0 PyPI"));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
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
