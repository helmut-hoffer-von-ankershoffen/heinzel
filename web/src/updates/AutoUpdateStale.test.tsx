// A version bump under an open page (heinzel-management-uke3): hz now serves another web build than the page runs,
// whoever updated it. The page reloads itself when that is safe, keeps unsent text, and says what happened.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { mockApi } from "@/test/render";
import { AutoUpdate, type AutoUpdateStatus, BUILD_CHECK_MS } from "./AutoUpdate";
import { updateTransport } from "./controller";
import { useUpdateDraft } from "./draft";
import { registerRunningWork } from "./work";

const OLD = ["/assets/index-old.css", "/assets/index-old.js"];
const NEW = ["/assets/index-new.css", "/assets/index-new.js"];
let running = OLD;
vi.mock("./webBuild", async (original) => ({
  ...(await original<typeof import("./webBuild")>()),
  pageEntrypoints: () => running,
}));

const status: AutoUpdateStatus = {
  enabled: true,
  reason: null,
  phase: "idle",
  version: "1.0.0",
  core: null,
  plugins: {},
  seconds_left: null,
  apply_at: null,
  next_window_at: null,
  postponements: 0,
  message: null,
};
const page = (paths: string[]) =>
  new Response(
    `<!doctype html><html><head><link rel="stylesheet" href="${paths[0]}"><script type="module" src="${paths[1]}"></script></head></html>`,
    { headers: { "Content-Type": "text/html" } },
  );

function UpdateComposer() {
  const { message, setMessage } = useUpdateDraft("route:/", null);
  return <textarea aria-label="Message" value={message} onChange={(event) => setMessage(event.target.value)} />;
}

let served = OLD;
let visibility: DocumentVisibilityState = "visible";
const reload = vi.fn();

function open() {
  sessionStorage.setItem("hz.token", "test-token");
  const api = mockApi({ "/api/auto-update": status, "/": () => page(served) });
  const view = render(
    <Providers client={queryClient()}>
      <UpdateComposer />
      <AutoUpdate />
    </Providers>,
  );
  return { ...api, ...view };
}

/** hz was updated and restarted elsewhere: it serves the new build now; the open page still runs the old one. */
const bump = () => {
  served = NEW;
};
const servedAsked = (calls: Request[]) => calls.filter((request) => new URL(request.url).pathname === "/").length;

beforeEach(() => {
  running = OLD;
  served = OLD;
  visibility = "visible";
  reload.mockReset();
  vi.spyOn(updateTransport, "reload").mockImplementation(reload);
  vi.spyOn(updateTransport, "clean").mockResolvedValue(undefined);
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("a stale web build under an open page", () => {
  it("is noticed when the window comes to the front, and reloads it once with a notice for the new page", async () => {
    const { calls, unmount } = open();
    await waitFor(() => expect(servedAsked(calls)).toBe(1));
    expect(reload).not.toHaveBeenCalled(); // the page runs the build hz serves
    bump();
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    expect(sessionStorage.getItem("hz.update.staleBuild")).toBe(NEW.join("\n"));
    unmount();
    // The reloaded page runs the new build: it says so once, and forgets the reload.
    running = NEW;
    const next = open();
    expect(await screen.findByText("Heinzel was updated, so this window reloaded.")).toBeInTheDocument();
    expect(screen.queryByText("Your unsent message is still here.")).toBeNull();
    await waitFor(() => expect(servedAsked(next.calls)).toBe(1));
    expect(sessionStorage.getItem("hz.update.staleBuild")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(screen.queryByText(/this window reloaded/)).toBeNull();
  });

  it("is noticed by the check every minute, also without focus or a server version change", async () => {
    const intervals = vi.spyOn(window, "setInterval");
    const { calls } = open();
    await waitFor(() => expect(servedAsked(calls)).toBe(1));
    const minutely = intervals.mock.calls.find(([, ms]) => ms === BUILD_CHECK_MS)?.[0];
    expect(minutely).toBeTypeOf("function");
    bump();
    act(() => (minutely as () => void)());
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
  });

  it("keeps unsent text in front, then saves it and reloads once the window goes to the background", async () => {
    const { calls, unmount } = open();
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Half a thought" } });
    await waitFor(() => expect(servedAsked(calls)).toBe(1));
    bump();
    act(() => window.dispatchEvent(new Event("focus")));
    expect(await screen.findByText(/Reload to finish the update/)).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    visibility = "hidden";
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    unmount();
    running = NEW;
    open();
    // The reloaded page has the text back and says so.
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("Half a thought");
    expect(await screen.findByText("Your unsent message is still here.")).toBeInTheDocument();
  });

  it("never reloads under a running turn or an open dialog, in front or not", async () => {
    let turn = true;
    const release = registerRunningWork(() => turn);
    try {
      const { calls } = open();
      await waitFor(() => expect(servedAsked(calls)).toBe(1));
      bump();
      visibility = "hidden";
      act(() => window.dispatchEvent(new Event("online")));
      expect(await screen.findByText(/once your message is sent and the answer is done/)).toBeInTheDocument();
      const dialog = document.body.appendChild(document.createElement("div"));
      dialog.setAttribute("role", "dialog");
      turn = false; // the answer is done, but a dialog is open now
      expect(
        await screen.findByText(/once the dialog or terminal is closed/, undefined, { timeout: 3000 }),
      ).toBeInTheDocument();
      expect(reload).not.toHaveBeenCalled();
      dialog.remove();
      await waitFor(() => expect(reload).toHaveBeenCalledOnce(), { timeout: 3000 });
    } finally {
      release();
    }
  });

  it("does not reload again for a build a reload did not bring, and offers the reload instead", async () => {
    sessionStorage.setItem("hz.update.staleBuild", NEW.join("\n"));
    served = NEW;
    open();
    expect(await screen.findByText(/runs an older Heinzel/)).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("asks nothing on a page without a build of its own (a dev server)", async () => {
    running = [];
    const { calls } = open();
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(servedAsked(calls)).toBe(0);
  });
});
