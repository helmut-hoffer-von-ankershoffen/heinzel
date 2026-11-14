// A version bump under an open page (heinzel-management-uke3): hz now serves another web build than the page runs,
// whoever updated it. The page reloads itself when that is safe, keeps unsent text, and says what happened.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { mockApi } from "@/test/render";
import { AutoUpdate, type AutoUpdateStatus, BUILD_CHECK_MS } from "./AutoUpdate";
import { updateTransport } from "./controller";
import { useUpdateDraft } from "./draft";
import { NOTICE_MS } from "./useUpdateReload";
import { forgetDraftRestored, registerRunningWork } from "./work";

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
  return (
    <textarea
      aria-label="Message"
      data-hz-restorable=""
      value={message}
      onChange={(event) => setMessage(event.target.value)}
    />
  );
}

let served = OLD;
let visibility: DocumentVisibilityState = "visible";
const reload = vi.fn();

/** A window with the chat composer, a field nothing saves, and the update cards. */
function open() {
  sessionStorage.setItem("hz.token", "test-token");
  const api = mockApi({ "/api/auto-update": status, "/": () => page(served) });
  const view = render(
    <Providers client={queryClient()}>
      <UpdateComposer />
      <input aria-label="Skill name" />
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
const online = () => act(() => window.dispatchEvent(new Event("online")));
const toBackground = () => {
  visibility = "hidden";
  act(() => document.dispatchEvent(new Event("visibilitychange")));
};

beforeEach(() => {
  running = OLD;
  served = OLD;
  visibility = "visible";
  forgetDraftRestored();
  reload.mockReset();
  vi.spyOn(updateTransport, "reload").mockImplementation(reload);
  vi.spyOn(updateTransport, "clean").mockResolvedValue(undefined);
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("a stale web build under an open page", () => {
  it("is noticed, reloads the window once, and the new page says so without claiming a kept message", async () => {
    const { calls, unmount } = open();
    await waitFor(() => expect(servedAsked(calls)).toBe(1));
    expect(reload).not.toHaveBeenCalled(); // the page runs the build hz serves
    bump();
    online();
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

  it("asks every minute, on coming back online, and on focus at most every ten seconds", async () => {
    const intervals = vi.spyOn(window, "setInterval");
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(1_000_000);
    const { calls } = open();
    await waitFor(() => expect(servedAsked(calls)).toBe(1));
    act(() => window.dispatchEvent(new Event("focus")));
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(servedAsked(calls)).toBe(1); // just asked
    now.mockReturnValue(1_000_000 + 10_000);
    act(() => window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(servedAsked(calls)).toBe(2));
    const minutely = intervals.mock.calls.find(([, ms]) => ms === BUILD_CHECK_MS)?.[0];
    expect(minutely).toBeTypeOf("function");
    bump();
    act(() => (minutely as () => void)());
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
  });

  it("keeps a chat message in front, then saves it, reloads in the background and says it was restored", async () => {
    const { calls, unmount } = open();
    fireEvent.input(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Half a thought" } });
    await waitFor(() => expect(servedAsked(calls)).toBe(1));
    bump();
    online();
    expect(await screen.findByText(/Reload to finish the update/)).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    toBackground();
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    unmount();
    running = NEW;
    open();
    // The reloaded page has the text back, and only because it has does it say so.
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("Half a thought");
    expect(await screen.findByText("Your unsent message is still here.")).toBeInTheDocument();
  });

  it("holds for text in a field nothing saves, in front and in the background", async () => {
    const { calls } = open();
    fireEvent.input(screen.getByRole("textbox", { name: "Skill name" }), { target: { value: "Weekly digest" } });
    await waitFor(() => expect(servedAsked(calls)).toBe(1));
    bump();
    online();
    expect(await screen.findByText("This window reloads once unsaved text is saved or cleared.")).toBeInTheDocument();
    toBackground();
    await new Promise((resolve) => setTimeout(resolve, 1200)); // one more check while held
    expect(reload).not.toHaveBeenCalled();
    fireEvent.input(screen.getByRole("textbox", { name: "Skill name" }), { target: { value: "" } });
    await waitFor(() => expect(reload).toHaveBeenCalledOnce(), { timeout: 3000 });
  });

  it("never reloads under a running turn or an open dialog, in front or not", async () => {
    let turn = true;
    const release = registerRunningWork(() => turn);
    try {
      const { calls } = open();
      await waitFor(() => expect(servedAsked(calls)).toBe(1));
      bump();
      visibility = "hidden";
      online();
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

  it("checks once more before going, and stays when hz went back to the build the page runs", async () => {
    const { calls } = open();
    fireEvent.input(screen.getByRole("textbox", { name: "Skill name" }), { target: { value: "Weekly digest" } });
    await waitFor(() => expect(servedAsked(calls)).toBe(1));
    bump();
    online();
    expect(await screen.findByText(/unsaved text is saved or cleared/)).toBeInTheDocument();
    served = OLD; // a restart fell back to the old build
    fireEvent.input(screen.getByRole("textbox", { name: "Skill name" }), { target: { value: "" } });
    await waitFor(() => expect(screen.queryByText(/unsaved text is saved or cleared/)).toBeNull(), { timeout: 3000 });
    expect(reload).not.toHaveBeenCalled();
  });

  it("does not loop under a server flapping between builds: two reloads, then it offers Reload", async () => {
    for (const build of [NEW, OLD]) {
      // Each round: the page runs one build, hz serves the other; the window reloads by itself.
      running = build === NEW ? OLD : NEW;
      served = build;
      const { unmount } = open();
      await waitFor(() => expect(reload).toHaveBeenCalled());
      reload.mockReset();
      unmount();
    }
    running = OLD;
    served = NEW;
    sessionStorage.removeItem("hz.update.staleBuild"); // a new build each time: only the per-tab cap stops it
    open();
    expect(await screen.findByText(/runs an older Heinzel version/)).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(reload).not.toHaveBeenCalled();
  });

  it("does not reload again for a build a reload did not bring, and offers the reload instead", async () => {
    sessionStorage.setItem("hz.update.staleBuild", NEW.join("\n"));
    served = NEW;
    open();
    expect(await screen.findByText(/runs an older Heinzel version/)).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("says why the offered Reload can't go while an answer is running", async () => {
    const release = registerRunningWork(() => true);
    try {
      sessionStorage.setItem("hz.update.staleBuild", NEW.join("\n"));
      served = NEW;
      open();
      fireEvent.click(await screen.findByRole("button", { name: "Reload" }));
      expect(screen.getByText(/once your message is sent and the answer is done/)).toBeInTheDocument();
      expect(reload).not.toHaveBeenCalled();
    } finally {
      release();
    }
  });

  it("keeps the notice of a reload in the background until the window is seen", async () => {
    const timers = vi.spyOn(window, "setTimeout");
    sessionStorage.setItem("hz.update.reloaded", "1");
    visibility = "hidden";
    open();
    expect(await screen.findByText(/this window reloaded/)).toBeInTheDocument();
    // The notice's own timer closes it; other timers of the page may share its length.
    const noticeTimer = () =>
      timers.mock.calls.some(([close, ms]) => ms === NOTICE_MS && String(close).includes("setShown"));
    expect(noticeTimer()).toBe(false);
    visibility = "visible";
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(noticeTimer()).toBe(true);
  });

  it("asks nothing on a page without a build of its own (a dev server)", async () => {
    running = [];
    const { calls } = open();
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(servedAsked(calls)).toBe(0);
  });
});
