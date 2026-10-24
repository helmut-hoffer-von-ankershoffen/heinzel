import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchemaChannel, SchemaTested } from "@/api/client";
import { OPEN_TERMINAL } from "@/lib/terminal";
import { base } from "@/test/chat";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const notice = {
  id: "20261024T0900-ab12",
  kind: "test" as const,
  title: "Notifications work",
  body: "",
  route: "/notifications",
  command: "hz system notify list",
  key: "test:1",
  urgent: true,
  at: "2026-10-24T09:00:00Z",
  due: "2026-10-24T09:00:00Z",
  popup: true,
};

const channel = (name: SchemaChannel["name"], state: SchemaChannel["state"], extra: Partial<SchemaChannel> = {}) =>
  ({ name, state, detail: `${name} detail`, fix: null, command: null, ...extra }) as SchemaChannel;

const tested = (channels: SchemaChannel[], native: string, pops_up: boolean): SchemaTested =>
  ({ notice, native, channels, pops_up }) as SchemaTested;

const nothing = tested(
  [
    channel("web", "ready"),
    channel("native", "blocked", { detail: "macOS doesn't allow Heinzel Notifier", fix: "open-settings" }),
    channel("daemon", "missing", { fix: "install-daemon", command: "hz system daemon install" }),
  ],
  "macOS doesn't allow Heinzel Notifier",
  true,
);

const answers = {
  ...base,
  "/api/system/notify/list": { notices: [], waiting: 0, native: "through Heinzel Notifier" },
  "/api/system/notify/settings": { opened: true, message: "open" },
};

afterEach(() => vi.restoreAllMocks());

describe("The notification test", () => {
  it("says when nothing pops up outside the app, and offers the fix of each blocked way", async () => {
    // hz answers a test nothing pops up with exit 1, which the web head sends as 503 with the result.
    const { calls } = mockApi({ ...answers, "/api/system/notify/test": { status: 503, body: nothing } });
    const user = userEvent.setup();
    const { container } = renderAt("/doctor?panel=notifications");
    await user.click(await screen.findByRole("button", { name: "Send a test notification" }));
    const result = await screen.findByRole("region", { name: "How the test went" });
    expect(within(result).getByRole("status")).toHaveTextContent("Nothing pops up outside this app");
    expect(result).toHaveTextContent("Blocked: macOS doesn't allow Heinzel Notifier");
    expect(result).toHaveTextContent("This browser can't show notifications.");
    await expectAccessible(container);

    await user.click(within(result).getByRole("button", { name: "Open macOS notification settings" }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/system/notify/settings"))).toBe(true));
    expect(screen.getByText("Fixed it? Try again.")).toBeInTheDocument();

    const opened = vi.fn();
    window.addEventListener(OPEN_TERMINAL, opened);
    await user.click(within(result).getByRole("button", { name: "Install daemon" }));
    const confirm = screen.getByRole("alertdialog", { name: "Run in the terminal" });
    expect(confirm).toHaveTextContent("hz system daemon install");
    await user.click(within(confirm).getByRole("button", { name: "Run it" }));
    expect(opened).toHaveBeenCalled();
    window.removeEventListener(OPEN_TERMINAL, opened);

    const test = calls.find((call) => call.url.includes("/system/notify/test"));
    expect(new URL(test?.url ?? "").searchParams.get("native")).toBe("true");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says so when the test popped up", async () => {
    const shown = tested(
      [channel("web", "ready"), channel("native", "shown"), channel("daemon", "ready")],
      "shown",
      true,
    );
    mockApi({ ...answers, "/api/system/notify/test": shown });
    const user = userEvent.setup();
    renderAt("/doctor?panel=notifications");
    await user.click(await screen.findByRole("button", { name: "Send a test notification" }));
    expect(await screen.findByRole("status")).toHaveTextContent("A test notification popped up.");
  });

  it("shows why a test couldn't be recorded at all", async () => {
    mockApi({
      ...answers,
      "/api/system/notify/test": {
        status: 400,
        body: { error: "Notifications are off: hz system config set system.notify.enabled true" },
      },
    });
    const user = userEvent.setup();
    renderAt("/doctor?panel=notifications");
    await user.click(await screen.findByRole("button", { name: "Send a test notification" }));
    expect(await screen.findByText(/Notifications are off/)).toHaveAttribute("role", "alert");
  });
});
