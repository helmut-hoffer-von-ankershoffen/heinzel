import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AudioVisualizer, spectrumLevel } from "./AudioVisualizer";
import { captureAudio, captureMicrophone, systemAudioAvailable } from "./capture";
import { publishOwnAudio } from "./own";
import { reloadSettings, setMaster, setStyle } from "./viz/settings";
import { setVizStage } from "./viz/stage";

vi.mock("./capture", () => ({
  captureAudio: vi.fn(),
  captureMicrophone: vi.fn(() => new Promise(() => undefined)),
  systemAudioAvailable: vi.fn(async () => false),
  hasDesktopAudio: vi.fn(
    () =>
      typeof (globalThis as { heinzelDesktop?: { systemAudio?: unknown } }).heinzelDesktop?.systemAudio === "function",
  ),
}));

const stop = vi.fn();
const captured = {
  analyser: { frequencyBinCount: 16, getByteFrequencyData: vi.fn(), disconnect: vi.fn() },
  stream: {
    getTracks: () => [{ addEventListener: vi.fn(), removeEventListener: vi.fn() }],
    getAudioTracks: () => [{ readyState: "live", addEventListener: vi.fn(), removeEventListener: vi.fn() }],
  },
  label: "Selected browser tab",
  stop,
} as unknown as Awaited<ReturnType<typeof captureAudio>>;

const micStop = vi.fn();
const listening = {
  ...captured,
  label: "Microphone",
  stop: micStop,
} as unknown as Awaited<ReturnType<typeof captureMicrophone>>;

const realMediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");
const realSecure = Object.getOwnPropertyDescriptor(globalThis, "isSecureContext");
beforeEach(() => {
  vi.mocked(systemAudioAvailable).mockResolvedValue(false);
  // A secure page (https or localhost), where browsers offer the microphone at all.
  Object.defineProperty(globalThis, "isSecureContext", { configurable: true, value: true });
  // A browser that offers tab sharing; the capture itself is mocked above.
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getDisplayMedia: vi.fn(), getUserMedia: vi.fn() },
  });
});

const open = () => fireEvent.click(screen.getByRole("button", { name: /^Audio (visualizer|sharing|request)/ }));
const pane = () => within(screen.getByRole("region", { name: "Audio visualizer controls" }));
const source = (name: string) =>
  within(screen.getByRole("radiogroup", { name: "Listen to" })).getByRole("radio", { name });

afterEach(() => {
  if (realSecure) Object.defineProperty(globalThis, "isSecureContext", realSecure);
  else delete (globalThis as { isSecureContext?: boolean }).isSecureContext;
  if (realMediaDevices) Object.defineProperty(navigator, "mediaDevices", realMediaDevices);
  else delete (navigator as { mediaDevices?: MediaDevices }).mediaDevices;
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  act(() => setVizStage(null));
  reloadSettings();
  act(() => publishOwnAudio({ mode: "none" }));
});

/** A recording 2D context on every canvas, a visible document and captured animation frames; `restore` undoes it. */
function paintable() {
  const gradient = { addColorStop: vi.fn() };
  const draw = {
    clearRect: vi.fn(),
    setTransform: vi.fn(),
    fillRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    closePath: vi.fn(),
    fill: vi.fn(),
    arc: vi.fn(),
    roundRect: vi.fn(),
    rect: vi.fn(),
    stroke: vi.fn(),
    createLinearGradient: vi.fn(() => gradient),
    createRadialGradient: vi.fn(() => gradient),
  };
  const getContext = vi
    .spyOn(HTMLCanvasElement.prototype, "getContext")
    .mockReturnValue(draw as unknown as CanvasRenderingContext2D);
  const oldVisibility = Object.getOwnPropertyDescriptor(document, "visibilityState");
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  const frames: FrameRequestCallback[] = [];
  const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    frames.push(callback);
    return frames.length;
  });
  const restore = () => {
    raf.mockRestore();
    getContext.mockRestore();
    if (oldVisibility) Object.defineProperty(document, "visibilityState", oldVisibility);
    else delete (document as { visibilityState?: DocumentVisibilityState }).visibilityState;
  };
  return { draw, frames, restore };
}

describe("AudioVisualizer", () => {
  it.each([
    ["Microphone", "the microphone"],
    ["Shared audio", "shared audio"],
  ])("discloses active %s when every style is off", async (choice, description) => {
    if (choice === "Microphone") vi.mocked(captureMicrophone).mockResolvedValueOnce(listening);
    else vi.mocked(captureAudio).mockResolvedValueOnce(captured);
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source(choice));
    await waitFor(() => expect(source(choice)).toHaveAttribute("aria-checked", "true"));
    act(() => {
      for (const style of ["spectrum", "aurora", "halo", "swarm"] as const) setStyle(style, false);
    });
    expect(screen.getByRole("status")).toHaveTextContent(`Listening to ${description}`);
    expect(screen.getByRole("button", { name: /^Audio sharing active/ })).toBeVisible();
  });

  it("discloses system capture when every style is off", async () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureAudio).mockResolvedValueOnce({ ...captured, label: "System audio" });
    try {
      render(<AudioVisualizer />);
      open();
      fireEvent.click(await waitFor(() => pane().getByRole("button", { name: "Use system audio" })));
      await waitFor(() => expect(pane().getByRole("button", { name: "Stop system audio" })).toBeVisible());
      act(() => {
        for (const style of ["spectrum", "aurora", "halo", "swarm"] as const) setStyle(style, false);
      });
      expect(screen.getByRole("status")).toHaveTextContent("Listening to system audio");
      expect(screen.getByRole("button", { name: /^Audio sharing active/ })).toBeVisible();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("discloses a pending request with all styles off and keeps Off available by keyboard", async () => {
    const user = userEvent.setup();
    vi.mocked(captureAudio).mockImplementationOnce(() => new Promise(() => undefined));
    render(<AudioVisualizer />);
    open();
    act(() => {
      for (const style of ["spectrum", "aurora", "halo", "swarm"] as const) setStyle(style, false);
    });
    fireEvent.click(source("Shared audio"));
    const signal = vi.mocked(captureAudio).mock.calls.at(-1)?.[0];
    expect(screen.getByRole("status")).toHaveTextContent("Waiting for the browser to allow shared audio");
    expect(source("Shared audio")).toHaveAttribute("aria-disabled", "true");
    expect(source("Microphone")).toHaveAttribute("aria-disabled", "true");
    expect(source("Off")).not.toHaveAttribute("aria-disabled");
    fireEvent.click(source("Microphone"));
    expect(captureMicrophone).not.toHaveBeenCalled();
    source("Shared audio").focus();
    fireEvent.keyDown(source("Shared audio"), { key: "ArrowRight" });
    expect(source("Off")).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(signal?.aborted).toBe(true);
    expect(source("Off")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Nothing shown: every style is off.");
  });

  it("discloses pending microphone capture when all styles are off", () => {
    vi.mocked(captureMicrophone).mockImplementationOnce(() => new Promise(() => undefined));
    render(<AudioVisualizer />);
    open();
    act(() => {
      for (const style of ["spectrum", "aurora", "halo", "swarm"] as const) setStyle(style, false);
    });
    fireEvent.click(source("Microphone"));
    expect(screen.getByRole("status")).toHaveTextContent("Waiting for the browser to allow microphone");
    expect(source("Microphone")).toHaveAttribute("aria-disabled", "true");
    expect(source("Off")).not.toHaveAttribute("aria-disabled");
  });

  it("discloses pending system capture when all styles are off and allows cancellation", async () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureAudio).mockImplementationOnce(() => new Promise(() => undefined));
    try {
      render(<AudioVisualizer />);
      open();
      act(() => {
        for (const style of ["spectrum", "aurora", "halo", "swarm"] as const) setStyle(style, false);
      });
      fireEvent.click(await waitFor(() => pane().getByRole("button", { name: "Use system audio" })));
      const signal = vi.mocked(captureAudio).mock.calls.at(-1)?.[0];
      expect(screen.getByRole("status")).toHaveTextContent("Waiting for the browser to allow system audio");
      fireEvent.click(pane().getByRole("button", { name: "Stop system audio" }));
      expect(signal?.aborted).toBe(true);
      expect(screen.getByRole("status")).toHaveTextContent("Nothing shown: every style is off.");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("keeps the active microphone disclosed during and after a refused switch", async () => {
    vi.mocked(captureMicrophone).mockResolvedValueOnce(listening);
    let reject: (error: Error) => void = () => {};
    vi.mocked(captureAudio).mockImplementationOnce(
      () =>
        new Promise((_resolve, fail) => {
          reject = fail;
        }),
    );
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Microphone"));
    await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
    act(() => {
      for (const style of ["spectrum", "aurora", "halo", "swarm"] as const) setStyle(style, false);
    });
    fireEvent.click(source("Shared audio"));
    expect(screen.getByRole("status")).toHaveTextContent("Listening to the microphone; waiting for shared audio");
    await act(async () => reject(new DOMException("Denied", "NotAllowedError")));
    expect(screen.getByRole("status")).toHaveTextContent("Still listening to the microphone");
    expect(micStop).not.toHaveBeenCalled();
  });
  it("paints a full-window, click-through aurora from the real analyser and sleeps when the sound stops", async () => {
    let loud = true;
    vi.mocked(captureAudio).mockResolvedValue({
      ...captured,
      analyser: {
        frequencyBinCount: 64,
        getByteFrequencyData: (data: Uint8Array) => data.fill(loud ? 220 : 0),
      },
    } as unknown as Awaited<ReturnType<typeof captureAudio>>);
    const { draw, frames, restore } = paintable();
    try {
      render(<AudioVisualizer />);
      expect(screen.queryByTestId("audio-aurora")).not.toBeInTheDocument();
      open();
      fireEvent.click(source("Shared audio"));
      const aurora = await screen.findByTestId("audio-aurora");
      expect(aurora).toHaveAttribute("aria-hidden", "true");
      expect(aurora).toHaveClass("pointer-events-none", "fixed", "inset-0", "-z-10");
      // Under the app surfaces, so text and cards stay crisp: never a positive layer above the content.
      expect(aurora.className).not.toMatch(/(^|\s)z-(\[|\d)/);
      await waitFor(() => expect(frames.length).toBeGreaterThan(0));
      for (let index = 0; index < 20; index += 1) act(() => frames.at(-1)?.(1000 + index * 40));
      expect(draw.fill).toHaveBeenCalled();
      expect(draw.stroke).toHaveBeenCalled();
      expect(draw.createRadialGradient).toHaveBeenCalled();
      // The 30 fps cap: a frame 10 ms after the last draws nothing.
      const drawn = draw.clearRect.mock.calls.length;
      act(() => frames.at(-1)?.(1000 + 19 * 40 + 10));
      expect(draw.clearRect.mock.calls.length).toBe(drawn);
      // Silence: the loop settles and stops scheduling frames instead of redrawing an empty canvas.
      loud = false;
      let time = 2000;
      for (let index = 0; index < 400 && frames.length > 0; index += 1) {
        const before = frames.length;
        time += 40;
        act(() => frames.at(-1)?.(time));
        if (frames.length === before) break;
      }
      const settled = frames.length;
      act(() => frames.at(-1)?.(time + 40));
      expect(frames.length).toBe(settled);
      // The status follows the sleeping loop: it never keeps claiming a signal over a cleared canvas.
      expect(screen.getByText("Listening to shared audio. Waiting for sound.")).toBeInTheDocument();
    } finally {
      restore();
    }
  });

  it("fits and paints the aurora again after the browser restores a lost canvas, playing or resting", async () => {
    let loud = true;
    vi.mocked(captureAudio).mockResolvedValue({
      ...captured,
      analyser: {
        frequencyBinCount: 64,
        getByteFrequencyData: (data: Uint8Array) => data.fill(loud ? 220 : 0),
      },
    } as unknown as Awaited<ReturnType<typeof captureAudio>>);
    const { draw, frames, restore } = paintable();
    try {
      render(<AudioVisualizer />);
      open();
      fireEvent.click(source("Shared audio"));
      const aurora = await screen.findByTestId("audio-aurora");
      await waitFor(() => expect(frames.length).toBeGreaterThan(0));
      let time = 1000;
      const step = () => {
        time += 40;
        act(() => frames.at(-1)?.(time));
      };
      for (let index = 0; index < 5; index += 1) step();
      // Playing: a lost and restored canvas is fitted again (its transform was reset) and painted on the next frame.
      act(() => {
        aurora.dispatchEvent(new Event("contextlost"));
        aurora.dispatchEvent(new Event("contextrestored"));
      });
      const fitted = draw.setTransform.mock.calls.length;
      const filled = draw.fill.mock.calls.length;
      step();
      expect(draw.setTransform.mock.calls.length).toBeGreaterThan(fitted);
      expect(draw.fill.mock.calls.length).toBeGreaterThan(filled);
      // Resting: the loop sleeps, so a restore must redraw the resting rail itself instead of leaving it blank.
      loud = false;
      for (let index = 0; index < 400; index += 1) {
        const before = frames.length;
        step();
        if (frames.length === before) break;
      }
      const sleeping = frames.length;
      const rested = draw.roundRect.mock.calls.length + draw.rect.mock.calls.length + draw.fillRect.mock.calls.length;
      act(() => screen.getByRole("img", { name: "Shared audio spectrum" }).dispatchEvent(new Event("contextrestored")));
      expect(
        draw.roundRect.mock.calls.length + draw.rect.mock.calls.length + draw.fillRect.mock.calls.length,
      ).toBeGreaterThan(rested);
      expect(frames.length).toBe(sleeping);
    } finally {
      restore();
    }
  });

  it("replaces every canvas when a lost context does not come back, then draws them in memory", async () => {
    vi.mocked(captureAudio).mockResolvedValue({
      ...captured,
      analyser: { frequencyBinCount: 64, getByteFrequencyData: (data: Uint8Array) => data.fill(220) },
    } as unknown as Awaited<ReturnType<typeof captureAudio>>);
    const { draw, frames, restore } = paintable();
    let lost = false;
    Object.assign(draw, { isContextLost: () => lost });
    const getContext = vi.mocked(HTMLCanvasElement.prototype.getContext);
    try {
      render(<AudioVisualizer />);
      open();
      fireEvent.click(source("Shared audio"));
      const first = await screen.findByTestId("audio-aurora");
      const rail = screen.getByRole("img", { name: "Shared audio spectrum" });
      await waitFor(() => expect(frames.length).toBeGreaterThan(0));
      act(() => frames.at(-1)?.(1000));
      // Lost and restored within the grace time: the browser recovered, the same canvases stay.
      lost = true;
      act(() => first.dispatchEvent(new Event("contextlost")));
      lost = false;
      act(() => first.dispatchEvent(new Event("contextrestored")));
      await new Promise((resolve) => setTimeout(resolve, 1100));
      expect(screen.getByTestId("audio-aurora")).toBe(first);
      // Lost for good, announced by no event: the frame notices, and after the grace time new canvases replace them.
      lost = true;
      act(() => frames.at(-1)?.(1100));
      await waitFor(() => expect(screen.getByTestId("audio-aurora")).not.toBe(first), { timeout: 3000 });
      lost = false;
      const second = screen.getByTestId("audio-aurora");
      expect(second.dataset.vizGeneration).toBe("1");
      expect(second.dataset.vizRender).toBe("default");
      expect(screen.getByRole("img", { name: "Shared audio spectrum" })).not.toBe(rail);
      // Lost again: the GPU keeps failing, so the next canvases draw in memory and say so.
      lost = true;
      act(() => second.dispatchEvent(new Event("contextlost")));
      await waitFor(() => expect(screen.getByTestId("audio-aurora")).not.toBe(second), { timeout: 3000 });
      lost = false;
      const third = screen.getByTestId("audio-aurora");
      expect(third.dataset.vizRender).toBe("memory");
      expect(getContext).toHaveBeenLastCalledWith("2d", { willReadFrequently: true });
      // The new canvases are drawn: the loop runs again on them.
      await waitFor(() => expect(frames.length).toBeGreaterThan(0));
      const filled = draw.fill.mock.calls.length;
      act(() => frames.at(-1)?.(5000));
      expect(draw.fill.mock.calls.length).toBeGreaterThan(filled);
    } finally {
      restore();
    }
  });

  it("analyses Heinzel's own sound with no prompt, and honours the master and style switches", () => {
    const analyser = {
      frequencyBinCount: 16,
      getByteFrequencyData: vi.fn(),
      disconnect: vi.fn(),
    } as unknown as AnalyserNode;
    act(() => publishOwnAudio({ mode: "analysed", analyser, label: "Rain" }));
    render(<AudioVisualizer />);
    expect(screen.getByRole("img", { name: "Shared audio spectrum" })).toBeInTheDocument();
    expect(captureAudio).not.toHaveBeenCalled();
    expect(captureMicrophone).not.toHaveBeenCalled();
    expect(screen.getByTestId("audio-aurora")).toBeInTheDocument();
    // Show is one choice: Aurora alone drops the rail and keeps the aurora layer, Spectrum alone the reverse.
    act(() => setStyle("spectrum", false));
    expect(screen.queryByRole("img", { name: "Shared audio spectrum" })).not.toBeInTheDocument();
    expect(screen.getByTestId("audio-aurora")).toBeInTheDocument();
    act(() => {
      setStyle("spectrum", true);
      setStyle("aurora", false);
    });
    expect(screen.getByRole("img", { name: "Shared audio spectrum" })).toBeInTheDocument();
    expect(screen.queryByTestId("audio-aurora")).not.toBeInTheDocument();
    act(() => setStyle("aurora", true));
    // The master switch hides every style but keeps its trigger, reading Off, so it can be turned on again here.
    act(() => setMaster(false));
    expect(screen.queryByRole("img", { name: "Shared audio spectrum" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("audio-aurora")).not.toBeInTheDocument();
    open();
    const master = screen.getByRole("switch", { name: "Audio visualisation" });
    expect(master).toHaveAttribute("aria-checked", "false");
    fireEvent.click(master);
    expect(screen.getByRole("img", { name: "Shared audio spectrum" })).toBeInTheDocument();
  });

  it("asks once, inline, to see music playing in a frame, and remembers the answer", () => {
    render(<AudioVisualizer />);
    expect(screen.queryByRole("complementary", { name: "Let Heinzel see the music" })).not.toBeInTheDocument();
    act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
    const prompt = screen.getByRole("complementary", { name: "Let Heinzel see the music" });
    expect(within(prompt).getByText("Let Heinzel see the music?")).toBeVisible();
    expect(captureAudio).not.toHaveBeenCalled();
    fireEvent.click(within(prompt).getByRole("button", { name: "Use microphone" }));
    expect(captureMicrophone).toHaveBeenCalledOnce();
    expect(captureAudio).not.toHaveBeenCalled();
  });

  it("makes quiet frequency bins visible without letting levels exceed the scale", () => {
    expect(spectrumLevel(16)).toBeCloseTo(0.25, 2);
    expect(spectrumLevel(255)).toBe(1);
    expect(spectrumLevel(-10)).toBe(0);
    expect(spectrumLevel(300)).toBe(1);
  });

  it("does not request capture until clicked, then exposes a stop control and stops the stream on unmount", async () => {
    vi.mocked(captureAudio).mockResolvedValue(captured);
    const view = render(<AudioVisualizer />);
    expect(captureAudio).not.toHaveBeenCalled();
    open();
    const controls = screen.getByRole("region", { name: "Audio visualizer controls" });
    expect(within(controls).getByText(/Nothing is recorded or uploaded/)).toBeVisible();
    expect(source("Off")).toHaveAttribute("aria-checked", "true");
    expect(captureAudio).not.toHaveBeenCalled();
    fireEvent.click(source("Shared audio"));
    await waitFor(() => expect(source("Shared audio")).toHaveAttribute("aria-checked", "true"));
    expect(captureAudio).toHaveBeenCalledOnce();
    view.unmount();
    expect(stop).toHaveBeenCalledOnce();
  });

  it("describes the workspace-only consent route when the desktop exposes it", () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true });
    try {
      render(<AudioVisualizer />);
      open();
      expect(source("Workspace audio")).toBeVisible();
      expect(screen.getByText(/Nothing is recorded or uploaded/)).toBeVisible();
      expect(screen.queryByText(/Share tab audio/)).not.toBeInTheDocument();
      expect(captureAudio).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("never offers System audio in a browser or where the desktop app cannot loop it back", async () => {
    render(<AudioVisualizer />);
    open();
    expect(systemAudioAvailable).not.toHaveBeenCalled();
    const group = within(screen.getByRole("radiogroup", { name: "Listen to" }));
    expect(group.getAllByRole("radio").map((radio) => radio.textContent)).toEqual([
      "Off",
      "Microphone",
      "Shared audio",
    ]);
    expect(screen.queryByRole("button", { name: /system audio|Try again/i })).not.toBeInTheDocument();
    cleanup();

    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    try {
      render(<AudioVisualizer />);
      open();
      // The default sources never wait for the desktop app's answer.
      expect(source("Workspace audio")).toBeVisible();
      await waitFor(() => expect(systemAudioAvailable).toHaveBeenCalledOnce());
      expect(
        within(screen.getByRole("radiogroup", { name: "Listen to" }))
          .getAllByRole("radio")
          .map((radio) => radio.textContent),
      ).toEqual(["Off", "Microphone", "Workspace audio"]);
      expect(screen.queryByRole("button", { name: /system audio/i })).not.toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("keeps workspace audio the default where the desktop app loops back, with system audio as an extra opt-in", async () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureAudio)
      .mockResolvedValueOnce(captured)
      .mockResolvedValueOnce({ ...captured, label: "System audio" });
    try {
      render(<AudioVisualizer />);
      act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
      open();
      const use = await waitFor(() => pane().getByRole("button", { name: "Use system audio" }));
      expect(
        within(screen.getByRole("radiogroup", { name: "Listen to" }))
          .getAllByRole("radio")
          .map((radio) => radio.textContent),
      ).toEqual(["Off", "Microphone", "Workspace audio"]);
      // The inline question offers the default sources only; system audio is never suggested there.
      const prompt = within(screen.getByRole("complementary", { name: "Let Heinzel see the music" }));
      expect(prompt.getAllByRole("button").map((button) => button.textContent)).toEqual([
        "Share this tab",
        "Use microphone",
        "Not now",
      ]);
      expect(captureAudio).not.toHaveBeenCalled();
      // The default: workspace audio, exactly as without loopback.
      fireEvent.click(source("Workspace audio"));
      await waitFor(() => expect(source("Workspace audio")).toHaveAttribute("aria-checked", "true"));
      expect(captureAudio).toHaveBeenLastCalledWith(expect.any(AbortSignal), false);
      expect(localStorage.getItem("hz.audio.source")).toBe("tab");
      // The opt-in: one explicit press, its own capture.
      fireEvent.click(use);
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("system audio"));
      expect(captureAudio).toHaveBeenLastCalledWith(expect.any(AbortSignal), true);
      expect(captureAudio).toHaveBeenCalledTimes(2);
      expect(stop).toHaveBeenCalledOnce();
      expect(captureMicrophone).not.toHaveBeenCalled();
      expect(localStorage.getItem("hz.audio.source")).toBe("system");
      for (const name of ["Off", "Microphone", "Workspace audio"])
        expect(source(name)).toHaveAttribute("aria-checked", "false");
      fireEvent.click(pane().getByRole("button", { name: "Stop system audio" }));
      expect(stop).toHaveBeenCalledTimes(2);
      expect(localStorage.getItem("hz.audio.source")).toBe("off");
      expect(pane().getByRole("button", { name: "Use system audio" })).toBeVisible();
      expect(source("Off")).toHaveAttribute("aria-checked", "true");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("lets system audio be stopped while permission is pending", async () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureAudio).mockImplementationOnce(() => new Promise(() => undefined));
    try {
      render(<AudioVisualizer />);
      open();
      fireEvent.click(await waitFor(() => pane().getByRole("button", { name: "Use system audio" })));
      const signal = vi.mocked(captureAudio).mock.calls.at(-1)?.[0];
      fireEvent.click(pane().getByRole("button", { name: "Stop system audio" }));
      expect(signal?.aborted).toBe(true);
      expect(pane().getByRole("button", { name: "Use system audio" })).toBeVisible();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("resumes a stored, granted microphone choice on a desktop with loopback, as before", async () => {
    localStorage.setItem("hz.audio.source", "mic");
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureMicrophone).mockResolvedValueOnce(listening);
    const realPermissions = Object.getOwnPropertyDescriptor(navigator, "permissions");
    const query = vi.fn(async () => ({ state: "granted" }));
    Object.defineProperty(navigator, "permissions", { configurable: true, value: { query } });
    try {
      render(<AudioVisualizer />);
      act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
      await waitFor(() => expect(captureMicrophone).toHaveBeenCalledOnce());
      expect(captureAudio).not.toHaveBeenCalled();
    } finally {
      if (realPermissions) Object.defineProperty(navigator, "permissions", realPermissions);
      else delete (navigator as { permissions?: Permissions }).permissions;
      vi.unstubAllGlobals();
    }
  });

  it("when system audio cannot work here, stops offering it with one line and keeps the default sources", async () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureAudio).mockRejectedValueOnce(new DOMException("Not supported", "NotSupportedError"));
    try {
      render(<AudioVisualizer />);
      open();
      fireEvent.click(await waitFor(() => pane().getByRole("button", { name: "Use system audio" })));
      await waitFor(() => expect(pane().getByText(/System audio does not work on this computer/)).toBeInTheDocument());
      expect(source("Workspace audio")).toBeVisible();
      expect(screen.queryByRole("button", { name: /system audio|Try again/i })).not.toBeInTheDocument();
      expect(sessionStorage.getItem("hz.audio.nosystem")).toBe("1");
    } finally {
      sessionStorage.removeItem("hz.audio.nosystem");
      vi.unstubAllGlobals();
    }
  });

  it("does not resume a remembered microphone after system audio fails, including after remount", async () => {
    localStorage.setItem("hz.audio.source", "mic");
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureAudio).mockRejectedValueOnce(new DOMException("Not supported", "NotSupportedError"));
    vi.mocked(captureMicrophone).mockResolvedValue(listening);
    const realPermissions = Object.getOwnPropertyDescriptor(navigator, "permissions");
    const query = vi.fn(async () => ({ state: "denied" }));
    Object.defineProperty(navigator, "permissions", { configurable: true, value: { query } });
    try {
      const view = render(<AudioVisualizer />);
      open();
      fireEvent.click(await waitFor(() => pane().getByRole("button", { name: "Use system audio" })));
      await waitFor(() => expect(pane().getByText(/System audio does not work on this computer/)).toBeInTheDocument());
      expect(localStorage.getItem("hz.audio.source")).toBe("off");
      query.mockResolvedValue({ state: "granted" });
      view.unmount();
      render(<AudioVisualizer />);
      act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
      await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
      expect(captureMicrophone).not.toHaveBeenCalled();
      open();
      fireEvent.click(source("Microphone"));
      await waitFor(() => expect(captureMicrophone).toHaveBeenCalledOnce());
    } finally {
      if (realPermissions) Object.defineProperty(navigator, "permissions", realPermissions);
      else delete (navigator as { permissions?: Permissions }).permissions;
      vi.unstubAllGlobals();
    }
  });

  it("after a declined system audio prompt shows one plain line and Try system audio again beside the defaults", async () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureAudio)
      .mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"))
      .mockResolvedValueOnce({ ...captured, label: "System audio" });
    try {
      render(<AudioVisualizer />);
      open();
      fireEvent.click(await waitFor(() => pane().getByRole("button", { name: "Use system audio" })));
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("System audio was not allowed."));
      expect(source("Workspace audio")).toBeVisible();
      expect(captureMicrophone).not.toHaveBeenCalled();
      fireEvent.click(pane().getByRole("button", { name: "Try system audio again" }));
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("system audio"));
      expect(captureAudio).toHaveBeenCalledTimes(2);
      expect(vi.mocked(captureAudio).mock.calls.every((call) => call[1] === true)).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("a declined workspace request never turns the system action into a retry", async () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureAudio).mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"));
    try {
      render(<AudioVisualizer />);
      open();
      await waitFor(() => pane().getByRole("button", { name: "Use system audio" }));
      fireEvent.click(source("Workspace audio"));
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("cancelled or blocked"));
      expect(captureAudio).toHaveBeenCalledExactlyOnceWith(expect.any(AbortSignal), false);
      expect(pane().getByRole("button", { name: "Use system audio" })).toBeVisible();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("reports permission failures and cancels an in-flight picker when disabled", async () => {
    vi.mocked(captureAudio).mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"));
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Shared audio"));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("cancelled or blocked"));
    const status = screen.getByRole("status");
    expect(status).toBeVisible();
    expect(status).not.toHaveClass("sr-only");
    cleanup();

    vi.mocked(captureAudio).mockImplementationOnce((_signal) => new Promise(() => undefined));
    const { rerender } = render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Shared audio"));
    const signal = vi.mocked(captureAudio).mock.calls.at(-1)?.[0];
    expect(signal?.aborted).toBe(false);
    rerender(<AudioVisualizer enabled={false} />);
    expect(signal?.aborted).toBe(true);
  });

  it("shows each style as its own checkbox chip, the on ones filled and checked, and the source as one choice", () => {
    act(() => setVizStage({ element: document.createElement("div"), playful: true }));
    render(<AudioVisualizer />);
    open();
    const show = within(screen.getByRole("group", { name: "Show" }));
    for (const name of ["Spectrum", "Aurora", "Halo", "Swarm"]) {
      expect(show.getByRole("checkbox", { name })).toBeChecked();
      expect(show.getByRole("checkbox", { name }).closest("label")).toHaveClass(
        "bg-primary",
        "text-primary-foreground",
      );
    }
    fireEvent.click(show.getByRole("checkbox", { name: "Halo" }));
    expect(show.getByRole("checkbox", { name: "Halo" })).not.toBeChecked();
    expect(show.getByRole("checkbox", { name: "Halo" }).closest("label")).not.toHaveClass("bg-primary");
    expect(show.getByRole("checkbox", { name: "Swarm" })).toBeChecked();
    expect(document.querySelectorAll("[aria-pressed]")).toHaveLength(0);
    // Every style off is allowed; the status line says so.
    for (const name of ["Spectrum", "Aurora", "Swarm"]) fireEvent.click(show.getByRole("checkbox", { name }));
    expect(screen.getByRole("status")).toHaveTextContent("Nothing shown: every style is off.");
    // The source is exclusive: radios.
    expect(source("Off")).toHaveAttribute("aria-checked", "true");
    expect(within(screen.getByRole("radiogroup", { name: "Listen to" })).queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("stands the Halo and the Swarm in the sidebar stage, only while the Heinzels play along", () => {
    const analyser = {
      frequencyBinCount: 16,
      getByteFrequencyData: vi.fn(),
      disconnect: vi.fn(),
    } as unknown as AnalyserNode;
    const element = document.createElement("div");
    document.body.append(element);
    try {
      act(() => publishOwnAudio({ mode: "analysed", analyser, label: "Rain" }));
      act(() => setVizStage({ element, playful: true }));
      render(<AudioVisualizer />);
      expect(element.querySelector("[data-testid=audio-halo]")).not.toBeNull();
      expect(element.querySelector("[data-testid=audio-swarm]")).not.toBeNull();
      expect(element.querySelectorAll("[data-swarm-figure]")).toHaveLength(3);
      act(() => setStyle("halo", false));
      expect(element.querySelector("[data-testid=audio-halo]")).toBeNull();
      expect(element.querySelector("[data-testid=audio-swarm]")).not.toBeNull();
      act(() => setVizStage({ element, playful: false }));
      expect(element.querySelector("[data-testid=audio-swarm]")).toBeNull();
    } finally {
      act(() => setVizStage(null));
      element.remove();
    }
  });

  it("switches from the microphone to shared audio in one step, stopping the microphone only once sharing runs", async () => {
    vi.mocked(captureMicrophone).mockResolvedValueOnce(listening);
    let grant: (value: typeof captured) => void = () => undefined;
    vi.mocked(captureAudio).mockImplementationOnce(() => new Promise((resolve) => (grant = resolve)));
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Microphone"));
    await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
    const microphoneSignal = vi.mocked(captureMicrophone).mock.calls.at(-1)?.[0];
    expect(source("Shared audio")).not.toHaveAttribute("aria-disabled");
    fireEvent.click(source("Shared audio"));
    expect(captureAudio).toHaveBeenCalledOnce();
    expect(microphoneSignal?.aborted).toBe(false);
    expect(micStop).not.toHaveBeenCalled();
    await act(async () => grant(captured));
    expect(micStop).toHaveBeenCalledOnce();
    expect(source("Shared audio")).toHaveAttribute("aria-checked", "true");
    // And back: the microphone is asked for, then sharing stops.
    vi.mocked(captureMicrophone).mockResolvedValueOnce(listening);
    fireEvent.click(source("Microphone"));
    await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
    expect(stop).toHaveBeenCalledOnce();
  });

  it("a refused microphone says so in its own words, captures nothing and can be chosen again", async () => {
    vi.mocked(captureMicrophone).mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"));
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Microphone"));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("The microphone was not allowed."));
    expect(screen.getByRole("status")).toHaveTextContent("Allow it for this site in your browser's settings");
    expect(screen.getByRole("status")).not.toHaveTextContent("sharing");
    expect(source("Off")).toHaveAttribute("aria-checked", "true");
    expect(captureAudio).not.toHaveBeenCalled();
    // Not a dead end: the choice is still there and asks again, once.
    vi.mocked(captureMicrophone).mockResolvedValueOnce(listening);
    fireEvent.click(source("Microphone"));
    await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
    expect(captureMicrophone).toHaveBeenCalledTimes(2);
  });

  it("keeps the running source when the new one is refused", async () => {
    vi.mocked(captureMicrophone).mockResolvedValueOnce(listening);
    vi.mocked(captureAudio).mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"));
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Microphone"));
    await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
    fireEvent.click(source("Shared audio"));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("cancelled or blocked"));
    expect(micStop).not.toHaveBeenCalled();
    expect(source("Microphone")).toHaveAttribute("aria-checked", "true");
  });

  it("lets Off stop the microphone and cancel a pending source switch", async () => {
    vi.mocked(captureMicrophone).mockResolvedValueOnce(listening);
    vi.mocked(captureAudio).mockImplementationOnce(() => new Promise(() => undefined));
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Microphone"));
    await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
    fireEvent.click(source("Shared audio"));
    const signal = vi.mocked(captureAudio).mock.calls.at(-1)?.[0];
    expect(source("Off")).not.toHaveAttribute("aria-disabled");
    fireEvent.click(source("Off"));
    expect(signal?.aborted).toBe(true);
    expect(micStop).toHaveBeenCalledOnce();
    expect(source("Off")).toHaveAttribute("aria-checked", "true");
  });

  it("keeps a replacement request alive when the old source ends", async () => {
    const track = new EventTarget();
    const addListener = vi.spyOn(track, "addEventListener");
    Object.assign(track, { readyState: "live" });
    vi.mocked(captureMicrophone).mockResolvedValueOnce({
      ...listening,
      stream: { getTracks: () => [track], getAudioTracks: () => [track] },
    } as unknown as typeof listening);
    let grant: (value: typeof captured) => void = () => undefined;
    vi.mocked(captureAudio).mockImplementationOnce(() => new Promise((resolve) => (grant = resolve)));
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Microphone"));
    await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
    const staleEnded = addListener.mock.calls.find(([event]) => event === "ended")?.[1] as EventListener;
    fireEvent.click(source("Shared audio"));
    const replacementSignal = vi.mocked(captureAudio).mock.calls.at(-1)?.[0];
    act(() => track.dispatchEvent(new Event("ended")));
    expect(replacementSignal?.aborted).toBe(false);
    await act(async () => grant(captured));
    expect(source("Shared audio")).toHaveAttribute("aria-checked", "true");
    act(() => staleEnded(new Event("ended")));
    expect(stop).not.toHaveBeenCalled();
  });

  it("does not restart an ended microphone when replacement sharing is unsupported", async () => {
    const track = new EventTarget();
    Object.assign(track, { readyState: "live" });
    vi.mocked(captureMicrophone).mockResolvedValueOnce({
      ...listening,
      stream: { getTracks: () => [track], getAudioTracks: () => [track] },
    } as unknown as typeof listening);
    let reject: (error: Error) => void = () => undefined;
    vi.mocked(captureAudio).mockImplementationOnce(() => new Promise((_, fail) => (reject = fail)));
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Microphone"));
    await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
    fireEvent.click(source("Shared audio"));
    act(() => track.dispatchEvent(new Event("ended")));
    await act(async () => reject(new DOMException("Not supported", "NotSupportedError")));
    expect(captureMicrophone).toHaveBeenCalledOnce();
    expect(source("Off")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Shared audio is not offered by this browser.")).toBeVisible();
  });

  it("stops the microphone with Off, remembers Off, and never restarts it by itself", async () => {
    localStorage.setItem("hz.audio.source", "mic");
    const realPermissions = Object.getOwnPropertyDescriptor(navigator, "permissions");
    Object.defineProperty(navigator, "permissions", {
      configurable: true,
      value: { query: vi.fn(async () => ({ state: "granted" })) },
    });
    try {
      vi.mocked(captureMicrophone).mockResolvedValue(listening);
      render(<AudioVisualizer />);
      act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
      // A microphone the person allowed and chose before resumes once, without a question.
      await waitFor(() => expect(captureMicrophone).toHaveBeenCalledOnce());
      open();
      await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
      fireEvent.click(source("Off"));
      expect(micStop).toHaveBeenCalledOnce();
      expect(source("Off")).toHaveAttribute("aria-checked", "true");
      expect(localStorage.getItem("hz.audio.source")).toBe("off");
      await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
      expect(captureMicrophone).toHaveBeenCalledOnce();
      expect(screen.queryByRole("complementary", { name: "Let Heinzel see the music" })).not.toBeInTheDocument();
    } finally {
      if (realPermissions) Object.defineProperty(navigator, "permissions", realPermissions);
      else delete (navigator as { permissions?: Permissions }).permissions;
    }
  });

  it("does not resume a remembered microphone while visualisation is switched off", async () => {
    localStorage.setItem("hz.audio.source", "mic");
    const realPermissions = Object.getOwnPropertyDescriptor(navigator, "permissions");
    Object.defineProperty(navigator, "permissions", {
      configurable: true,
      value: { query: vi.fn(async () => ({ state: "granted" })) },
    });
    try {
      act(() => setMaster(false));
      vi.mocked(captureMicrophone).mockResolvedValue(listening);
      render(<AudioVisualizer />);
      act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
      await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
      expect(captureMicrophone).not.toHaveBeenCalled();
      act(() => setMaster(true));
      await waitFor(() => expect(captureMicrophone).toHaveBeenCalledOnce());
    } finally {
      if (realPermissions) Object.defineProperty(navigator, "permissions", realPermissions);
      else delete (navigator as { permissions?: Permissions }).permissions;
    }
  });

  it("does not restart a manually started microphone after its track ends", async () => {
    const realPermissions = Object.getOwnPropertyDescriptor(navigator, "permissions");
    Object.defineProperty(navigator, "permissions", {
      configurable: true,
      value: { query: vi.fn(async () => ({ state: "granted" })) },
    });
    const track = new EventTarget();
    Object.assign(track, { readyState: "live" });
    const microphone = {
      ...listening,
      stream: { getTracks: () => [track], getAudioTracks: () => [track] },
    } as unknown as typeof listening;
    try {
      vi.mocked(captureMicrophone).mockResolvedValue(microphone);
      render(<AudioVisualizer />);
      act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
      open();
      fireEvent.click(source("Microphone"));
      await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
      act(() => track.dispatchEvent(new Event("ended")));
      await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
      expect(captureMicrophone).toHaveBeenCalledOnce();
      expect(source("Off")).toHaveAttribute("aria-checked", "true");
    } finally {
      if (realPermissions) Object.defineProperty(navigator, "permissions", realPermissions);
      else delete (navigator as { permissions?: Permissions }).permissions;
    }
  });

  it("offers shared audio where the browser can share a tab (desktop Chromium)", () => {
    render(<AudioVisualizer />);
    open();
    expect(source("Shared audio")).toBeVisible();
    expect(screen.getByText(/sharing a tab shows your browser's sharing bar/)).toBeVisible();
  });

  it("does not offer shared audio on an iPhone, where no browser can share a tab", () => {
    // Safari, Chrome and Firefox on iOS are all WebKit without getDisplayMedia.
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia: vi.fn() } });
    render(<AudioVisualizer />);
    act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
    const prompt = screen.getByRole("complementary", { name: "Let Heinzel see the music" });
    expect(within(prompt).queryByRole("button", { name: "Share this tab" })).not.toBeInTheDocument();
    expect(within(prompt).getByRole("button", { name: "Use microphone" })).toBeVisible();
    open();
    const group = within(screen.getByRole("radiogroup", { name: "Listen to" }));
    expect(group.getAllByRole("radio").map((radio) => radio.textContent)).toEqual(["Off", "Microphone"]);
    expect(screen.queryByText(/sharing a tab/)).not.toBeInTheDocument();
    expect(screen.getByText("Nothing is recorded or uploaded. The microphone hears the room.")).toBeVisible();
    expect(captureAudio).not.toHaveBeenCalled();
  });

  it("stops offering shared audio for the session when the browser refuses it as unsupported, and listens instead", async () => {
    vi.mocked(captureAudio).mockRejectedValueOnce(new DOMException("Not supported", "NotSupportedError"));
    vi.mocked(captureMicrophone).mockResolvedValueOnce(listening);
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Shared audio"));
    await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
    expect(captureMicrophone).toHaveBeenCalledOnce();
    expect(
      screen.getByText("Shared audio is not offered by this browser, so Heinzel listens with the microphone."),
    ).toBeVisible();
    expect(
      within(screen.getByRole("radiogroup", { name: "Listen to" })).queryByRole("radio", { name: "Shared audio" }),
    ).not.toBeInTheDocument();
    expect(sessionStorage.getItem("hz.audio.unshared")).toBe("1");
    // Quietly: no red problem and no "Retry" on the trigger for an option that cannot work here.
    expect(screen.getByRole("status")).not.toHaveClass("text-bad");
    expect(screen.queryByRole("button", { name: /problem|Retry/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Retry")).not.toBeInTheDocument();
    // A cancelled or refused prompt is not "unsupported": that keeps the choice (covered above).
  });

  it("treats Safari's missing audio capture the same: hidden, the microphone instead, no error", async () => {
    // What iPhone Safari answered the owner: the capture itself reports that audio sharing is unavailable.
    vi.mocked(captureAudio).mockRejectedValueOnce(
      new DOMException(
        "Audio sharing is unavailable. Your browser or operating system may not support audio capture.",
        "NotSupportedError",
      ),
    );
    vi.mocked(captureMicrophone).mockResolvedValueOnce(listening);
    render(<AudioVisualizer />);
    open();
    fireEvent.click(source("Shared audio"));
    await waitFor(() => expect(source("Microphone")).toHaveAttribute("aria-checked", "true"));
    expect(screen.queryByText(/Audio sharing is unavailable/)).not.toBeInTheDocument();
    expect(screen.queryByText("Retry")).not.toBeInTheDocument();
  });

  it("shows no microphone choice without getUserMedia or outside a secure context", () => {
    Object.defineProperty(globalThis, "isSecureContext", { configurable: true, value: false });
    render(<AudioVisualizer />);
    open();
    const group = within(screen.getByRole("radiogroup", { name: "Listen to" }));
    expect(group.getAllByRole("radio").map((radio) => radio.textContent)).toEqual(["Off", "Shared audio"]);
    expect(
      screen.getByText("Nothing is recorded or uploaded. Sharing a tab shows your browser's sharing bar."),
    ).toBeVisible();
    cleanup();
    Object.defineProperty(globalThis, "isSecureContext", { configurable: true, value: true });
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getDisplayMedia: vi.fn() } });
    render(<AudioVisualizer />);
    open();
    expect(
      within(screen.getByRole("radiogroup", { name: "Listen to" }))
        .getAllByRole("radio")
        .map((radio) => radio.textContent),
    ).toEqual(["Off", "Shared audio"]);
    expect(
      screen.getByText("Nothing is recorded or uploaded. Sharing a tab shows your browser's sharing bar."),
    ).toBeVisible();
  });

  it("shows no Listen to group when this browser can take no source, and says so", () => {
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: {} });
    render(<AudioVisualizer />);
    act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
    expect(screen.queryByRole("complementary", { name: "Let Heinzel see the music" })).not.toBeInTheDocument();
    open();
    expect(screen.queryByRole("radiogroup", { name: "Listen to" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("cannot be seen in this browser");
    expect(
      screen.getByText("Nothing is recorded or uploaded. This browser shows Heinzel's own sound only."),
    ).toBeVisible();
  });

  it("shows only the styles this page can draw: no Halo or Swarm without the sidebar stage, none without a canvas", () => {
    render(<AudioVisualizer />);
    open();
    const names = () =>
      within(screen.getByRole("group", { name: "Show" }))
        .getAllByRole("checkbox")
        .map((box) => box.closest("label")?.textContent);
    // No sidebar stage here (or playful off): the Heinzel styles cannot stand anywhere.
    expect(names()).toEqual(["Spectrum", "Aurora"]);
    cleanup();
    const element = document.createElement("div");
    act(() => setVizStage({ element, playful: true }));
    try {
      render(<AudioVisualizer />);
      open();
      expect(names()).toEqual(["Spectrum", "Aurora", "Halo", "Swarm"]);
    } finally {
      act(() => setVizStage(null));
    }
    cleanup();
    const getContext = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, "getContext");
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", { configurable: true, value: undefined });
    try {
      render(<AudioVisualizer />);
      open();
      expect(screen.queryByRole("group", { name: "Show" })).not.toBeInTheDocument();
    } finally {
      if (getContext) Object.defineProperty(HTMLCanvasElement.prototype, "getContext", getContext);
    }
  });
});
