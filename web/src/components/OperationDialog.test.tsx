import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { daemonStatusKey } from "@/api/daemon";
import { DAEMON_INSTALL, DAEMON_UNINSTALL } from "@/lib/operationCalls";
import { requestOperation } from "@/lib/operationRun";
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
    const dialog = await screen.findByRole("alertdialog", {
      name: "Keep my Heinzelmännchen working while the app is closed.",
    });
    expect(dialog).toHaveTextContent("registers a background service that starts whenever you sign in");
    expect(dialog).toHaveTextContent("hz system daemon install");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(fetchMock).not.toHaveBeenCalled();
    await expectAccessible(container);
    await userEvent.setup().click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
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

    const dialog = screen.getByRole("alertdialog");
    await plan(live.phase);
    await live.phase("session", "running");
    expect(within(dialog).getByRole("progressbar", { name: "Steps" })).toHaveAttribute("aria-valuenow", "0");
    expect(within(dialog).getByRole("progressbar")).toHaveAttribute("aria-valuetext", "Step 1 of 4");
    await live.phase("session", "done");
    await live.phase("unit", "running");
    expect(within(dialog).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
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
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(live.fetchMock).toHaveBeenCalledOnce();

    for (const id of ["unit", "load", "verify"]) {
      await live.phase(id, "running");
      await live.phase(id, "done");
    }
    await live.end("result", { data: { dry_run: false } });
    expect(await screen.findByText("Installed. Its first check comes within 5 minutes.")).toBeInTheDocument();
    expect(within(dialog).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "4");
    // The footer and the daemon controls ask again for the state, which is what they show.
    await waitFor(() => {
      const keys = invalidate.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
      expect(keys).toContain(JSON.stringify(["ui"]));
      expect(keys).toContain(JSON.stringify(daemonStatusKey));
    });
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
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
    const dialog = screen.getByRole("alertdialog");
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
    const dialog = await screen.findByRole("alertdialog", { name: "Stop the background service?" });
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
    expect(await screen.findByRole("alertdialog")).toHaveAttribute("data-operation", "/system/daemon/install");
  });
});
