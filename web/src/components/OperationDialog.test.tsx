import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { daemonStatusKey } from "@/api/daemon";
import { DAEMON_INSTALL, DAEMON_RESTART, DAEMON_UNINSTALL } from "@/lib/operationCalls";
import {
  dismissOperation,
  type Phase,
  requestDrivenOperation,
  requestOperation,
  trackOperation,
} from "@/lib/operationRun";
import { NeedsAttention } from "@/lifecycle/applySteps";
import { expectAccessible } from "@/test/render";
import { OperationDialog } from "./OperationDialog";

afterEach(() => vi.unstubAllGlobals());

/** A server-sent stream the test writes to as the "server" does its steps. */
function liveStream() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  const fetchMock = vi.fn(async () => new Response(body, { headers: { "Content-Type": "text/event-stream" } }));
  vi.stubGlobal("fetch", fetchMock);
  const send = (kind: string, data: object) =>
    act(async () => {
      controller.enqueue(encoder.encode(`event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  const phase = (id: string, state: string, detail?: string) =>
    send("phase", { id, label: LABELS[id], state, ...(detail ? { detail } : {}) });
  const end = (kind: "result" | "error", data: object) =>
    act(async () => {
      controller.enqueue(encoder.encode(`event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`));
      controller.close();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  const close = () =>
    act(async () => {
      controller.close();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  return { fetchMock, send, phase, end, close };
}

const LABELS: Record<string, string> = {
  session: "Checking this is your Mac's desktop session",
  unit: "Writing the service definition",
  load: "Loading it into launchd",
  verify: "Checking that it is loaded",
};

function mount() {
  sessionStorage.setItem("hz.token", "test-token");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const view = render(
    <QueryClientProvider client={client}>
      <OperationDialog />
    </QueryClientProvider>,
  );
  return { ...view, invalidate };
}

const plan = async (phase: ReturnType<typeof liveStream>["phase"]) => {
  for (const id of Object.keys(LABELS)) await phase(id, "pending");
};

describe("OperationDialog", () => {
  it("asks first, names the service it registers and runs nothing until the person confirms", async () => {
    const { fetchMock } = liveStream();
    const { container } = mount();
    act(() => requestOperation(DAEMON_INSTALL));
    const dialog = await screen.findByRole("dialog", {
      name: "Keep my Heinzelmännchen working while the app is closed.",
    });
    expect(dialog).toHaveTextContent("registers a background service that starts whenever you sign in");
    expect(dialog).toHaveTextContent("hz system daemon install");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(fetchMock).not.toHaveBeenCalled();
    await expectAccessible(container);
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("restarts the daemon at once, without the exact call, and says plainly how it ended", async () => {
    const live = liveStream();
    mount();
    act(() => requestOperation(DAEMON_RESTART));
    expect(await screen.findByText("Restarting the daemon…", { selector: "output" })).toBeInTheDocument();
    expect(live.fetchMock).toHaveBeenCalledOnce();
    expect(screen.queryByText(/This changes something/)).not.toBeInTheDocument();
    expect(screen.queryByText("hz system daemon restart")).not.toBeInTheDocument();
    await live.end("error", { error: "previous handover cannot be verified" });
    const failed = await screen.findByRole("alert");
    expect(failed).toHaveTextContent("The daemon could not restart. Try again in a moment.");
    expect(failed).not.toHaveTextContent("handover");
  });

  it("shows the real steps as they happen, starts once however often it is clicked, and cannot be cancelled", async () => {
    const live = liveStream();
    const { invalidate } = mount();
    const user = userEvent.setup();
    act(() => requestOperation(DAEMON_INSTALL));
    await user.click(await screen.findByRole("button", { name: "Install background service" }));
    expect(live.fetchMock).toHaveBeenCalledOnce();
    const request = live.fetchMock.mock.calls[0] as unknown as [Request];
    expect(new URL(request[0].url).pathname).toBe("/api/stream/system/daemon/install");
    expect(request[0].method).toBe("POST");

    const dialog = screen.getByRole("dialog");
    await plan(live.phase);
    await live.phase("session", "running");
    expect(within(dialog).getByRole("progressbar", { name: "Steps" })).toHaveAttribute("value", "0");
    expect(within(dialog).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "Step 1 of 4");
    await live.phase("session", "done");
    await live.phase("unit", "running");
    expect(within(dialog).getByRole("progressbar")).toHaveAttribute("value", "1");
    expect(within(dialog).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "Step 2 of 4");
    const steps = within(dialog).getByRole("list", { name: "Steps" });
    expect(
      within(steps)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      "Checking this is your Mac's desktop session: done",
      "Writing the service definition: in progress",
      "Loading it into launchd: waiting",
      "Checking that it is loaded: waiting",
    ]);
    // Running: no way to double-start or to stop halfway, and Escape does not close it.
    const working = within(dialog).getByRole("button", { name: "Installing the background service" });
    expect(working).toBeDisabled();
    expect(within(dialog).queryByRole("button", { name: /Cancel|Close/ })).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent("can't be stopped halfway");
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(live.fetchMock).toHaveBeenCalledOnce();

    for (const id of ["unit", "load", "verify"]) {
      await live.phase(id, "running");
      await live.phase(id, "done");
    }
    await live.end("result", { data: { dry_run: false } });
    expect(await screen.findByText("Installed. Its first check comes within 5 minutes.")).toBeInTheDocument();
    expect(within(dialog).getByRole("progressbar")).toHaveAttribute("value", "4");
    // The footer and the daemon controls ask again for the state, which is what they show.
    await waitFor(() => {
      const keys = invalidate.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
      expect(keys).toContain(JSON.stringify(["ui"]));
      expect(keys).toContain(JSON.stringify(daemonStatusKey));
    });
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("says specifically what failed and what it did not change, and can try again", async () => {
    const first = liveStream();
    mount();
    const user = userEvent.setup();
    act(() => requestOperation(DAEMON_INSTALL));
    await user.click(await screen.findByRole("button", { name: "Install background service" }));
    await plan(first.phase);
    await first.phase("session", "running");
    await first.phase("session", "failed", "SSH is not the desktop");
    await first.end("error", {
      error: "macOS only lets a login service be registered from your Mac's desktop session. Nothing was changed.",
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Nothing was changed.");
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Checking this is your Mac's desktop session/)).toHaveTextContent("failed");
    expect(within(dialog).getByText("SSH is not the desktop")).toBeInTheDocument();
    expect(dialog).toHaveFocus();

    const second = liveStream();
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(second.fetchMock).toHaveBeenCalledOnce();
    await second.end("result", { data: {} });
    expect(await screen.findByText(/Installed\./)).toBeInTheDocument();
  });

  it("says the outcome is uncertain when the connection ends without an answer", async () => {
    const live = liveStream();
    mount();
    act(() => requestOperation(DAEMON_UNINSTALL));
    const dialog = await screen.findByRole("dialog", { name: "Stop the background service?" });
    expect(dialog).toHaveTextContent("Your receipts and logs stay");
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Uninstall" }));
    await live.close();
    expect(await screen.findByRole("alert")).toHaveTextContent("The stream ended without a result.");
    expect(dialog).toHaveTextContent("the outcome is not certain");
  });

  it("ignores a second request while one is being asked about or runs", async () => {
    liveStream();
    mount();
    act(() => requestOperation(DAEMON_INSTALL));
    act(() => requestOperation(DAEMON_UNINSTALL));
    expect(await screen.findByRole("dialog")).toHaveAttribute("data-operation", "/system/daemon/install");
  });

  it("shows a write the app drives with the steps it reports, and never retries it here", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    mount();
    const call = {
      path: "/system/update",
      method: "POST" as const,
      query: {},
      command: "hz system update",
      summary: "Updating",
      read_only: false,
    };
    let report!: (phases: { id: string; label: string; state: "running" | "warn" | "done" }[]) => void;
    let fail!: (error: Error) => void;
    let run!: Promise<unknown>;
    await act(async () => {
      run = trackOperation(
        call,
        (tell) =>
          new Promise<never>((_resolve, reject) => {
            report = tell;
            fail = reject;
          }),
      ).catch(() => undefined);
    });
    const dialog = await screen.findByRole("dialog", { name: "Updating" });
    expect(dialog).toHaveAttribute("data-tracked");
    act(() =>
      report([
        { id: "install", label: "Installing the update", state: "done" },
        { id: "plugin:acme", label: "Plugin acme", state: "warn" },
        { id: "restart", label: "Restarting hz", state: "running" },
      ]),
    );
    expect(within(dialog).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "Step 3 of 3");
    expect(within(dialog).getByText("Plugin acme")).toBeInTheDocument();
    await act(async () => {
      fail(new Error("Restart failed."));
      await run;
    });
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Restart failed.");
    expect(within(dialog).queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    act(() => dismissOperation());
  });

  describe("a write the app drives", () => {
    const call = {
      path: "/system/plugin/add",
      method: "POST" as const,
      query: {},
      command: "hz system plugin add heinzel-crm --confirm",
      summary: "Install heinzel-crm",
      read_only: false,
    };
    const drive = (run: (report: (phases: Phase[]) => void) => Promise<unknown>) => ({
      consent: ["Runs as code on this computer with your permissions.", "hz restarts and this page reloads itself."],
      runningTitle: "Installing heinzel-crm",
      doneTitle: "Installed heinzel-crm",
      running: "Installing…",
      action: "Install",
      run,
    });
    const plan = (states: Phase["state"][]): Phase[] =>
      ["preserve", "write", "restart", "wait", "reload", "check"].map((id, index) => ({
        id,
        label: `Step ${id}`,
        state: states[index] ?? "pending",
      }));

    it("asks once in the dialog, then shows the same dialog's progress in place and ends with Done", async () => {
      mount();
      const user = userEvent.setup();
      let report!: (phases: Phase[]) => void;
      let finish!: () => void;
      const run = vi.fn(
        (tell: (phases: Phase[]) => void) =>
          new Promise<void>((resolve) => {
            report = tell;
            finish = resolve;
          }),
      );
      act(() => requestDrivenOperation(call, drive(run)));
      const dialog = await screen.findByRole("dialog", { name: "Install heinzel-crm" });
      expect(dialog).toHaveTextContent("Runs as code on this computer with your permissions.");
      expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
      const row = within(dialog)
        .getAllByRole("button")
        .map((button) => button.textContent?.trim() || button.getAttribute("aria-label"));
      expect(row).toEqual(["Close", "Cancel", "Install"]);
      expect(run).not.toHaveBeenCalled();
      await user.click(within(dialog).getByRole("button", { name: "Install" }));
      expect(run).toHaveBeenCalledOnce();
      act(() => report(plan(["done", "running"])));
      const running = screen.getByRole("dialog", { name: "Installing heinzel-crm" });
      expect(running).toBe(dialog);
      expect(within(dialog).queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
      expect(within(dialog).queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Installing…" })).toBeDisabled();
      expect(within(dialog).getByText("Step 2 of 6: Step write")).toBeInTheDocument();
      expect(within(dialog).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "Step 2 of 6");
      await user.keyboard("{Escape}");
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      await act(async () => {
        report(plan(["done", "done", "done", "done", "done", "done"]));
        finish();
      });
      const done = await screen.findByRole("dialog", { name: "Installed heinzel-crm" });
      expect(within(done).getByRole("button", { name: "Done" })).toHaveFocus();
      await user.click(within(done).getByRole("button", { name: "Done" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("needs the person's attention when hz has not come back, and keeps waiting on their word", async () => {
      mount();
      const user = userEvent.setup();
      const run = vi
        .fn<(report: (phases: Phase[]) => void) => Promise<unknown>>()
        .mockImplementationOnce(async (report) => {
          report(plan(["done", "done", "done", "warn"]));
          throw new NeedsAttention("hz has not come back yet.");
        })
        .mockImplementationOnce(async (report) => {
          report(plan(["done", "done", "done", "done", "done", "done"]));
        });
      act(() => requestDrivenOperation(call, drive(run)));
      await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Install" }));
      const dialog = await screen.findByRole("dialog");
      expect(await within(dialog).findByRole("status")).toHaveTextContent("hz has not come back yet.");
      expect(within(dialog).queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Keep waiting" })).toHaveFocus();
      await user.click(within(dialog).getByRole("button", { name: "Keep waiting" }));
      expect(await screen.findByRole("dialog", { name: "Installed heinzel-crm" })).toBeInTheDocument();
      expect(run).toHaveBeenCalledTimes(2);
    });

    it("names the failed step and offers Try again, which runs the same write again", async () => {
      mount();
      const user = userEvent.setup();
      const run = vi.fn(async (report: (phases: Phase[]) => void) => {
        report(plan(["done", "failed"]));
        throw new Error("Could not download heinzel-crm.");
      });
      act(() => requestDrivenOperation(call, drive(run)));
      await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Install" }));
      const dialog = await screen.findByRole("dialog");
      expect(await within(dialog).findByRole("alert")).toHaveTextContent("Could not download heinzel-crm.");
      expect(within(dialog).getByText(/Step write/)).toHaveTextContent("failed");
      await user.click(within(dialog).getByRole("button", { name: "Try again" }));
      expect(run).toHaveBeenCalledTimes(2);
      act(() => dismissOperation());
    });
  });
});
