import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/app/providers";
import { useDurableDraft } from "@/chat/durableDraft";
import { expectAccessible, mockApi } from "@/test/render";
import { AutoUpdate, type AutoUpdateStatus, secondsLeft, updateSummary } from "./AutoUpdate";
import { updateTransport } from "./controller";
import { registerReloadHold } from "./work";

/** A composer on the real hook the chat uses, so the real reload hold is what is tested. */
function DurableComposer() {
  const { state, update } = useDurableDraft("session-1");
  return (
    <textarea aria-label="Message" value={state.text} onChange={(event) => update({ text: event.target.value })} />
  );
}

const base: AutoUpdateStatus = {
  enabled: true,
  reason: null,
  phase: "countdown",
  version: "1.0.0",
  core: "1.1.0",
  plugins: { "heinzel-example": "2.0.0" },
  seconds_left: 30,
  apply_at: "2026-11-02T09:00:30Z",
  next_window_at: null,
  postponements: 0,
  message: null,
};

function show(answers: Parameters<typeof mockApi>[0]) {
  sessionStorage.setItem("hz.token", "test-token");
  const api = mockApi(answers);
  const view = render(
    <Providers client={queryClient()}>
      <AutoUpdate />
    </Providers>,
  );
  return { ...api, ...view };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** The reload hold re-checks on a 1 s interval; the tests step it instead of sleeping on a loaded machine. */
function stepReloadChecks() {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  return (ms: number) => act(() => vi.advanceTimersByTime(ms));
}

function oldFrontendFixture() {
  sessionStorage.removeItem("hz.frontend.skew-reload");
  const script = document.createElement("script");
  script.type = "module";
  script.src = "/assets/old.js";
  document.body.append(script);
  return {
    build: {
      version: "1.1.0",
      identity: "a".repeat(64),
      entrypoints: ["/assets/new.js"],
      files: { "/assets/new.js": "b".repeat(64) },
    },
    close: () => {
      script.remove();
      sessionStorage.removeItem("hz.frontend.skew-reload");
    },
  };
}

function readyFrontend(build: ReturnType<typeof oldFrontendFixture>["build"]) {
  vi.spyOn(updateTransport, "frontend").mockResolvedValue(build);
  const assets = vi.spyOn(updateTransport, "assets").mockResolvedValue(undefined);
  const clean = vi.spyOn(updateTransport, "clean").mockResolvedValue(undefined);
  const reload = vi.spyOn(updateTransport, "reload").mockImplementation(() => undefined);
  return { assets, clean, reload };
}

describe("automatic updates", () => {
  it("shows the server's countdown with what will be installed and one Postpone button", async () => {
    // A frozen clock: the countdown ticks from Date.now, so a slow render must not turn 30 s into 29 s.
    vi.useFakeTimers({ toFake: ["Date"] });
    const { container } = show({ "/api/auto-update": base });
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("hz updates itself in 30 s");
    expect(dialog).toHaveTextContent("Installing: hz 1.1.0, heinzel-example 2.0.0.");
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Postpone 1 hour" })).toBeEnabled();
    await expectAccessible(container);
  });

  it("asks the server to postpone, so every window and a reload agree", async () => {
    const { calls } = show({
      "/api/auto-update": base,
      "/api/auto-update/postpone": { ...base, phase: "waiting", seconds_left: null, postponements: 1 },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Postpone 1 hour" }));
    await vi.waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    const posted = calls.find((call) => call.url.endsWith("/api/auto-update/postpone"));
    expect(posted?.method).toBe("POST");
    expect(posted?.headers.get("Authorization")).toBe("Bearer test-token");
  });

  it("shows one plain message after a failure until it is read", async () => {
    const message = "The automatic update didn't work, so hz 1.0.0 keeps running as before.";
    const { calls } = show({
      "/api/auto-update": { ...base, phase: "waiting", seconds_left: null, message },
      "/api/auto-update/dismiss": { ...base, phase: "waiting", seconds_left: null, message: null },
    });
    expect(await screen.findByRole("status")).toHaveTextContent(message);
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await vi.waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    expect(calls.some((call) => call.url.endsWith("/api/auto-update/dismiss"))).toBe(true);
  });

  it("shows nothing when off or nothing is newer", async () => {
    const { calls } = show({ "/api/auto-update": { ...base, phase: "off", seconds_left: null } });
    await vi.waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("detects old renderer assets even when the first backend status is already new, and waits for open work", async () => {
    const fixture = oldFrontendFixture();
    const { build } = fixture;
    const { assets, reload } = readyFrontend(build);
    let holding = true;
    const release = registerReloadHold(() => holding);
    try {
      show({ "/api/auto-update": { ...base, version: "1.1.0", phase: "idle", seconds_left: null } });
      await waitFor(() => expect(assets).toHaveBeenCalledWith(build, expect.any(AbortSignal), false));
      expect(await screen.findByText(/reload.*after/i)).toBeInTheDocument();
      expect(reload).not.toHaveBeenCalled();
      expect(sessionStorage.getItem("hz.frontend.skew-reload")).toBeNull();
      holding = false;
      await waitFor(() => expect(reload).toHaveBeenCalledOnce(), { timeout: 3000 });
    } finally {
      release();
      fixture.close();
    }
  });

  it("does not reload if work starts while cleanup is in flight", async () => {
    const fixture = oldFrontendFixture();
    const { build } = fixture;
    const { clean, reload } = readyFrontend(build);
    let finish!: () => void;
    clean.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    let holding = false;
    const release = registerReloadHold(() => holding);
    try {
      show({ "/api/auto-update": { ...base, version: "1.1.0", phase: "idle", seconds_left: null } });
      await waitFor(() => expect(clean).toHaveBeenCalled());
      holding = true;
      await act(async () => {
        finish();
        await Promise.resolve();
      });
      expect(reload).not.toHaveBeenCalled();
      expect(sessionStorage.getItem("hz.frontend.skew-reload")).toBeNull();
      holding = false;
      await waitFor(() => expect(reload).toHaveBeenCalledOnce(), { timeout: 3000 });
    } finally {
      release();
      fixture.close();
    }
  });

  it("invalidates held skew verification when another update starts", async () => {
    const step = stepReloadChecks();
    const fixture = oldFrontendFixture();
    const { build } = fixture;
    const { assets, reload } = readyFrontend(build);
    let holding = true;
    const release = registerReloadHold(() => holding);
    let server = { ...base, version: "1.1.0", phase: "idle" as AutoUpdateStatus["phase"], seconds_left: null };
    const client = queryClient();
    sessionStorage.setItem("hz.token", "test-token");
    mockApi({ "/api/auto-update": () => server });
    try {
      render(
        <Providers client={client}>
          <AutoUpdate />
        </Providers>,
      );
      await waitFor(() => expect(assets).toHaveBeenCalled());
      expect(await screen.findByText(/reload.*after/i)).toBeInTheDocument();
      server = { ...server, phase: "applying" };
      await act(() => client.refetchQueries({ queryKey: ["web", "auto-update"] }));
      holding = false;
      step(2000);
      expect(reload).not.toHaveBeenCalled();
      expect(sessionStorage.getItem("hz.frontend.skew-reload")).toBeNull();
    } finally {
      release();
      fixture.close();
    }
  });

  it("retries a transient frontend check without overlapping probes", async () => {
    const fixture = oldFrontendFixture();
    const { build } = fixture;
    let fail!: (error: Error) => void;
    const frontend = vi
      .spyOn(updateTransport, "frontend")
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            fail = reject;
          }),
      )
      .mockResolvedValue(build);
    const assets = vi.spyOn(updateTransport, "assets").mockResolvedValue(undefined);
    let retry: (() => void) | undefined;
    const timer = window.setTimeout.bind(window);
    vi.spyOn(window, "setTimeout").mockImplementation((handler, delay, ...args) => {
      if (delay === 30_000 && typeof handler === "function") {
        retry = () => handler(...args);
        return timer(() => {}, 2_147_483_647) as unknown as ReturnType<typeof setTimeout>;
      }
      return timer(handler, delay, ...args) as unknown as ReturnType<typeof setTimeout>;
    });
    const release = registerReloadHold(() => true);
    try {
      show({ "/api/auto-update": { ...base, version: "1.1.0", phase: "idle", seconds_left: null } });
      await waitFor(() => expect(frontend).toHaveBeenCalledTimes(1));
      expect(retry).toBeUndefined();
      await act(async () => {
        fail(new Error("temporary"));
        await Promise.resolve();
      });
      await waitFor(() => expect(retry).toBeDefined());
      act(() => retry?.());
      await waitFor(() => expect(assets).toHaveBeenCalledTimes(1));
      expect(frontend).toHaveBeenCalledTimes(2);
    } finally {
      release();
      fixture.close();
    }
  });

  it("does not reload if the server has no frontend descriptor", async () => {
    const script = document.createElement("script");
    script.type = "module";
    script.src = "/assets/old.js";
    document.body.append(script);
    const frontend = vi.spyOn(updateTransport, "frontend").mockRejectedValue(new Error("not supported"));
    const reload = vi.spyOn(updateTransport, "reload").mockImplementation(() => undefined);
    try {
      show({ "/api/auto-update": { ...base, version: "1.1.0", phase: "idle", seconds_left: null } });
      await waitFor(() => expect(frontend).toHaveBeenCalledOnce());
      expect(reload).not.toHaveBeenCalled();
    } finally {
      script.remove();
    }
  });

  it("reloads once the server restarted on another version", async () => {
    const reload = vi.spyOn(updateTransport, "reload").mockImplementation(() => undefined);
    vi.spyOn(updateTransport, "clean").mockResolvedValue(undefined);
    let version = "1.0.0";
    const client = queryClient();
    sessionStorage.setItem("hz.token", "test-token");
    mockApi({ "/api/auto-update": () => ({ ...base, phase: "applying", version, seconds_left: null }) });
    render(
      <Providers client={client}>
        <AutoUpdate />
      </Providers>,
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Updating hz");
    version = "1.1.0";
    await act(() => client.refetchQueries({ queryKey: ["web", "auto-update"] }));
    await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce());
  });

  it("waits to reload while the composer has unsent text or a turn runs, then reloads once idle", async () => {
    const reload = vi.spyOn(updateTransport, "reload").mockImplementation(() => undefined);
    vi.spyOn(updateTransport, "clean").mockResolvedValue(undefined);
    let typing = true;
    const step = stepReloadChecks();
    const release = registerReloadHold(() => typing);
    let version = "1.0.0";
    const client = queryClient();
    sessionStorage.setItem("hz.token", "test-token");
    mockApi({
      "/api/auto-update": () => ({
        ...base,
        phase: "idle",
        version,
        seconds_left: null,
        message: `hz is up to date: ${version}.`,
      }),
    });
    render(
      <Providers client={client}>
        <AutoUpdate />
      </Providers>,
    );
    // Query data arrives before React's scheduled notification; observe the original rendered version first.
    await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("hz is up to date: 1.0.0."));
    version = "1.1.0";
    await act(() => client.refetchQueries({ queryKey: ["web", "auto-update"] }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Reload to finish the update."));
    step(3000); // three checks while held
    expect(reload).not.toHaveBeenCalled(); // the unsent text stays
    typing = false; // sent, and the answer is done
    step(1000);
    await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce());
    release();
  });

  it("keeps the page while a real durable draft composer has unsent text, and reloads once it is cleared", async () => {
    const reload = vi.spyOn(updateTransport, "reload").mockImplementation(() => undefined);
    vi.spyOn(updateTransport, "clean").mockResolvedValue(undefined);
    const step = stepReloadChecks();
    let version = "1.0.0";
    const client = queryClient();
    sessionStorage.setItem("hz.token", "test-token");
    mockApi({
      "/api/auto-update": () => ({
        ...base,
        phase: "idle",
        version,
        seconds_left: null,
        message: `hz is up to date: ${version}.`,
      }),
    });
    render(
      <Providers client={client}>
        <DurableComposer />
        <AutoUpdate />
      </Providers>,
    );
    const box = screen.getByRole("textbox", { name: "Message" });
    fireEvent.change(box, { target: { value: "Half a thought" } });
    // The composer can render before AutoUpdate receives its first version.
    await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("hz is up to date: 1.0.0."));
    version = "1.1.0"; // the server restarted on the new version
    await act(() => client.refetchQueries({ queryKey: ["web", "auto-update"] }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Reload to finish the update."));
    step(3000); // three checks while held
    expect(reload).not.toHaveBeenCalled();
    expect(box).toHaveValue("Half a thought");
    fireEvent.change(box, { target: { value: "" } }); // sent or cleared
    step(1000);
    await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce());
  });

  it("counts down between polls from what the server said", () => {
    expect(secondsLeft(base, 1_000, 11_500)).toBe(20);
    expect(secondsLeft(base, 1_000, 99_000)).toBe(0);
    expect(secondsLeft({ ...base, phase: "waiting" }, 0, 0)).toBeNull();
    expect(updateSummary({ ...base, core: null })).toBe("heinzel-example 2.0.0");
  });
});
