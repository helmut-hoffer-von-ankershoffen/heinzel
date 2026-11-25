import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { recordSurface } from "@/lib/surfaceDiagnostics";
import { AboutDialog, clientHere, desktopApp, detailsText } from "./AboutDialog";
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
      <StrictMode>
        <AboutDialog />
      </StrictMode>
    </QueryClientProvider>,
  );

afterEach(() => {
  vi.restoreAllMocks();
  current = info;
});

describe("About dialog", () => {
  it("only exposes native About details to the owned desktop frame", () => {
    // The focused web lane owns the About surface and can run this dependency-free IPC suite with its pinned Node.
    const suite = resolve(dirname(fileURLToPath(import.meta.url)), "../../../desktop/test/lifecycle-ipc.test.js");
    execFileSync(process.execPath, ["--test", suite], { encoding: "utf8", timeout: 3_000 });
  });

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

  it("shows the team of Heinzelmännchen as the logo, not a single one", () => {
    show();
    act(() => requestMenuAction("about"));
    const dialog = screen.getByRole("dialog", { name: "Heinzelmännchen" });
    const logo = within(dialog).getByRole("img", { name: "Heinzelmännchen" });
    expect(within(logo).getByTestId("heinzel-swarm")).toHaveClass("size-12");
    // The swarm draws `overflow-visible`; the header slot clips it to its 48px, as the single mascot was.
    expect(logo).toHaveClass("size-12", "overflow-hidden");
    expect(logo).not.toHaveClass("overflow-visible");
    expect(logo.querySelector("[data-view]")).toBeNull();
  });

  it("names the installed desktop app's variant, Native or Electron, with its version and path", () => {
    const native = { path: "/Applications/Heinzelmaennchen.app", variant: "native", version: "0.9.53" };
    expect(desktopApp({ ...info, desktop_app: native } as never, null)).toEqual({
      name: "Native app 0.9.53",
      path: "/Applications/Heinzelmaennchen.app",
    });
    const electron = { ...native, variant: "electron" };
    expect(desktopApp({ ...info, desktop_app: electron } as never, null)?.name).toBe("Electron app 0.9.53");
    expect(desktopApp({ ...info, desktop_app: { ...native, variant: null } } as never, null)?.name).toBe(
      "Desktop app 0.9.53",
    );
    expect(desktopApp(info as never, null)).toBeNull();
  });

  it("tells which client shows the page from the bridge it injects", () => {
    const about = () => Promise.resolve({ version: "1", path: "/A" });
    expect(clientHere(undefined)).toBe("browser");
    expect(clientHere({})).toBe("native");
    expect(clientHere({ about })).toBe("electron");
    expect(clientHere({ about, workspaceAudio: false })).toBe("native");
    expect(clientHere({ about, clientFlavor: "native" })).toBe("native");
    expect(clientHere({ clientFlavor: "electron" })).toBe("electron");
  });

  it("shows the Native app row and the client it runs in", async () => {
    current = {
      ...info,
      desktop_app: { path: "/Applications/Heinzelmaennchen.app", variant: "native", version: "0.9.53" },
    };
    Object.defineProperty(window, "heinzelDesktop", { value: {}, configurable: true });
    try {
      show();
      act(() => requestMenuAction("about"));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("Native app 0.9.53")).toBeInTheDocument();
      expect(within(dialog).getByText("/Applications/Heinzelmaennchen.app")).toBeInTheDocument();
      expect(within(dialog).getByText("Running in").nextSibling).toHaveTextContent("Native app");
    } finally {
      Reflect.deleteProperty(window, "heinzelDesktop");
    }
  });

  it("gives the hz --version lines plus daemon, and the desktop app when there is one", () => {
    const text = detailsText(info as never, { version: "0.9.29", path: "/Applications/H.app" });
    expect(text).toContain("hz 1.2.3\n");
    expect(text).toContain("installation uv tool (installed 2026-11-08)");
    expect(text).toContain("daemon running 1.2.3");
    expect(text).toContain("desktop Electron app 0.9.29 /Applications/H.app");
    expect(text).toContain("client Browser");
    expect(text).toContain("plugin heinzel-b 0.4.0 git");
  });

  it("labels local Native About details when the backend has no desktop app metadata", async () => {
    const about = async () => ({ version: "0.9.53", path: "/Applications/Heinzelmaennchen.app" });
    vi.stubGlobal("heinzelDesktop", { about, clientFlavor: "native" });
    try {
      show();
      act(() => requestMenuAction("about"));
      const dialog = await screen.findByRole("dialog");
      expect(await within(dialog).findByText("Native app 0.9.53")).toBeInTheDocument();
      expect(within(dialog).getByText("Running in").nextSibling).toHaveTextContent("Native app");
      expect(detailsText(info as never, await about())).toContain("desktop Native app 0.9.53");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("includes the loaded entrypoint and safe surface snapshot separately from backend version", () => {
    const text = detailsText(
      info as never,
      { version: "0.9.29", path: "/Applications/H.app" },
      "/assets/index-old.js",
      ["browser surface: overlay; bridge: ok; native last sample: unavailable"],
    );
    expect(text).toContain("hz 1.2.3");
    expect(text).toContain("desktop Electron app 0.9.29");
    expect(text).toContain("loaded frontend entrypoint /assets/index-old.js");
    expect(text).toContain("browser surface: overlay");
  });

  it("copies the latest surface sample after About has opened", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    recordSurface("browser", "layout requested", "ok", { focused: true, visible: true, status: "ready" });
    show();
    act(() => requestMenuAction("about"));
    recordSurface("browser", "overlay", "ok", { focused: true, visible: false, status: "ready" });
    await userEvent.click(screen.getByRole("button", { name: /copy details/i }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("before overlay: layout requested"));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("view-visible=false"));
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
      `${info.repository}/releases/tag/v${info.version}`,
    );
    expect(within(dialog).getByRole("link", { name: "Source repository" })).toHaveAttribute("href", info.repository);
    expect(screen.getByRole("cell", { name: "heinzel-b" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /copy details/i }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("plugin heinzel-a 0.4.0 PyPI"));
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
      expect(within(dialog).getByRole("button", { name: "Copy details" })).toHaveFocus();
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
      expect(within(dialog).getByRole("button", { name: "Copy details" })).toBeDisabled();
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
