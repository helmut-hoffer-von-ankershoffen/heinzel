import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AudioVisualizer, spectrumLevel } from "./AudioVisualizer";
import { captureAudio, captureMicrophone } from "./capture";
import { publishOwnAudio } from "./own";
import { reloadSettings, setMaster, setStyle } from "./viz/settings";
import { setVizStage } from "./viz/stage";

vi.mock("./capture", () => ({
  captureAudio: vi.fn(),
  captureMicrophone: vi.fn(() => new Promise(() => undefined)),
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
beforeEach(() => {
  // A browser that offers tab sharing; the capture itself is mocked above.
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getDisplayMedia: vi.fn(), getUserMedia: vi.fn() },
  });
});

const open = () => fireEvent.click(screen.getByRole("button", { name: /^Audio (visualizer|sharing|request)/ }));
const source = (name: string) =>
  within(screen.getByRole("radiogroup", { name: "Listen to" })).getByRole("radio", { name });

afterEach(() => {
  if (realMediaDevices) Object.defineProperty(navigator, "mediaDevices", realMediaDevices);
  else delete (navigator as { mediaDevices?: MediaDevices }).mediaDevices;
  vi.clearAllMocks();
  localStorage.clear();
  reloadSettings();
  act(() => publishOwnAudio({ mode: "none" }));
});

describe("AudioVisualizer", () => {
  it("paints a full-window, click-through aurora from the real analyser and sleeps when the sound stops", async () => {
    let loud = true;
    vi.mocked(captureAudio).mockResolvedValue({
      ...captured,
      analyser: {
        frequencyBinCount: 64,
        getByteFrequencyData: (data: Uint8Array) => data.fill(loud ? 220 : 0),
      },
    } as unknown as Awaited<ReturnType<typeof captureAudio>>);
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
      raf.mockRestore();
      getContext.mockRestore();
      if (oldVisibility) Object.defineProperty(document, "visibilityState", oldVisibility);
      else delete (document as { visibilityState?: DocumentVisibilityState }).visibilityState;
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

  it("shows each style as its own toggle, the on ones filled, and the source as one choice", () => {
    render(<AudioVisualizer />);
    open();
    const show = within(screen.getByRole("group", { name: "Show" }));
    for (const name of ["Spectrum", "Aurora", "Halo", "Swarm"]) {
      expect(show.getByRole("button", { name })).toHaveAttribute("aria-pressed", "true");
      expect(show.getByRole("button", { name })).toHaveClass("bg-primary", "text-primary-foreground");
    }
    fireEvent.click(show.getByRole("button", { name: "Halo" }));
    expect(show.getByRole("button", { name: "Halo" })).toHaveAttribute("aria-pressed", "false");
    expect(show.getByRole("button", { name: "Halo" })).not.toHaveClass("bg-primary");
    expect(show.getByRole("button", { name: "Swarm" })).toHaveAttribute("aria-pressed", "true");
    // The source is exclusive: radios, never pressed buttons.
    expect(source("Off")).toHaveAttribute("aria-checked", "true");
    expect(within(screen.getByRole("radiogroup", { name: "Listen to" })).queryAllByRole("button")).toHaveLength(0);
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
    expect(source("Shared audio")).not.toHaveAttribute("aria-disabled");
    fireEvent.click(source("Shared audio"));
    expect(captureAudio).toHaveBeenCalledOnce();
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

  it("keeps shared audio in the choice when the browser cannot share, with the reason", () => {
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia: vi.fn() } });
    render(<AudioVisualizer />);
    open();
    const shared = source("Shared audio");
    expect(shared).toHaveAttribute("aria-disabled", "true");
    expect(shared).toHaveAttribute("title", "Shared audio is not offered by this browser.");
    fireEvent.click(shared);
    expect(captureAudio).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Shared audio is not offered by this browser.");
  });
});
