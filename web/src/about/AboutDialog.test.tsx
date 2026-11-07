import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AboutDialog, detailsText, pluginsOf } from "./AboutDialog";
import { requestMenuAction } from "./menu";

const info = {
  version: "1.2.3",
  repository: "https://github.com/helmut-hoffer-von-ankershoffen/heinzel",
  python_version: "3.14.1",
  python_executable: "/py",
  platform: "macOS",
  installation: "uv tool",
  latest_release: null,
  plugins: [
    { name: "heinzel-a", version: "0.4.0", source: "PyPI", repository: null },
    { name: "heinzel-b", version: "0.4.0", source: "git", repository: null },
  ],
};

vi.mock("@/api/queries", () => ({ useInfo: () => ({ data: info, isError: false }) }));

const show = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AboutDialog />
    </QueryClientProvider>,
  );

afterEach(() => vi.restoreAllMocks());

describe("About dialog", () => {
  it("lists the installed plugins the API reports", () => {
    expect(pluginsOf(info as never).map((p) => p.name)).toEqual(["heinzel-a", "heinzel-b"]);
  });

  it("gives the hz --version lines plus daemon, and the desktop app when there is one", () => {
    const text = detailsText(info as never, { version: "0.9.29", path: "/Applications/H.app" });
    expect(text).toContain("hz 1.2.3\n");
    expect(text).toContain("installation uv tool");
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
});
