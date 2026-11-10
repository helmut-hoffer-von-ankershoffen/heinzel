import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AudioVisualizer, spectrumLevel } from "./AudioVisualizer";
import { captureAudio, captureMicrophone, systemAudioAvailable } from "./capture";
import { publishOwnAudio } from "./own";
import { reloadSettings, setMaster, setShown } from "./viz/settings";

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
beforeEach(() => {
  vi.mocked(systemAudioAvailable).mockResolvedValue(false);
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
  if (realMediaDevices) Object.defineProperty(navigator, "mediaDevices", realMediaDevices);
  else delete (navigator as { mediaDevices?: MediaDevices }).mediaDevices;
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
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
    act(() => setShown("aurora"));
    expect(screen.queryByRole("img", { name: "Shared audio spectrum" })).not.toBeInTheDocument();
    expect(screen.getByTestId("audio-aurora")).toBeInTheDocument();
    act(() => setShown("spectrum"));
    expect(screen.getByRole("img", { name: "Shared audio spectrum" })).toBeInTheDocument();
    expect(screen.queryByTestId("audio-aurora")).not.toBeInTheDocument();
    act(() => setShown("both"));
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
      // Unknown until the desktop app answers: no source choice flashes in, then the usual ones appear.
      expect(screen.queryByRole("radiogroup", { name: "Listen to" })).not.toBeInTheDocument();
      await waitFor(() => expect(source("Workspace audio")).toBeVisible());
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

  it("makes system audio the only source where the desktop app loops it back, and captures it on click", async () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureAudio).mockResolvedValueOnce({ ...captured, label: "System audio" });
    try {
      render(<AudioVisualizer />);
      act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
      open();
      const use = await waitFor(() => pane().getByRole("button", { name: "Use system audio" }));
      expect(screen.queryByRole("radiogroup", { name: "Listen to" })).not.toBeInTheDocument();
      for (const name of ["Off", "Microphone", "Workspace audio", "Shared audio"])
        expect(screen.queryByRole("radio", { name })).not.toBeInTheDocument();
      const prompt = within(screen.getByRole("complementary", { name: "Let Heinzel see the music" }));
      expect(prompt.getAllByRole("button").map((button) => button.textContent)).toEqual([
        "Use system audio",
        "Not now",
      ]);
      expect(screen.getByRole("switch")).toBeInTheDocument();
      fireEvent.click(use);
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("system audio"));
      expect(captureAudio).toHaveBeenCalledExactlyOnceWith(expect.any(AbortSignal), true);
      expect(captureMicrophone).not.toHaveBeenCalled();
      expect(localStorage.getItem("hz.audio.source")).toBe("system");
      expect(screen.queryByRole("button", { name: "Use system audio" })).not.toBeInTheDocument();
      expect(screen.queryByRole("complementary", { name: "Let Heinzel see the music" })).not.toBeInTheDocument();
      fireEvent.click(pane().getByRole("button", { name: "Stop listening" }));
      expect(stop).toHaveBeenCalledOnce();
      expect(localStorage.getItem("hz.audio.source")).toBe("off");
      expect(pane().getByRole("button", { name: "Use system audio" })).toBeVisible();
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
      fireEvent.click(pane().getByRole("button", { name: "Stop listening" }));
      expect(signal?.aborted).toBe(true);
      expect(pane().getByRole("button", { name: "Use system audio" })).toBeVisible();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("never lets a stored microphone choice override system audio on the desktop", async () => {
    localStorage.setItem("hz.audio.source", "mic");
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    const realPermissions = Object.getOwnPropertyDescriptor(navigator, "permissions");
    const query = vi.fn(async () => ({ state: "granted" }));
    Object.defineProperty(navigator, "permissions", { configurable: true, value: { query } });
    try {
      render(<AudioVisualizer />);
      act(() => publishOwnAudio({ mode: "frame", label: "Hyper Focus" }));
      open();
      await waitFor(() => pane().getByRole("button", { name: "Use system audio" }));
      await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
      expect(captureMicrophone).not.toHaveBeenCalled();
      expect(query).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "Use microphone" })).not.toBeInTheDocument();
      expect(screen.queryByRole("radio", { name: "Microphone" })).not.toBeInTheDocument();
      expect(localStorage.getItem("hz.audio.source")).toBe("mic");
    } finally {
      if (realPermissions) Object.defineProperty(navigator, "permissions", realPermissions);
      else delete (navigator as { permissions?: Permissions }).permissions;
      vi.unstubAllGlobals();
    }
  });

  it("when system audio cannot work here, offers the other sources with one line instead of a dead-end retry", async () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true, systemAudio: vi.fn() });
    vi.mocked(systemAudioAvailable).mockResolvedValue(true);
    vi.mocked(captureAudio).mockRejectedValueOnce(new DOMException("Not supported", "NotSupportedError"));
    try {
      render(<AudioVisualizer />);
      open();
      fireEvent.click(await waitFor(() => pane().getByRole("button", { name: "Use system audio" })));
      await waitFor(() => expect(pane().getByRole("radiogroup", { name: "Listen to" })).toBeInTheDocument());
      expect(pane().getByText(/System audio does not work on this computer/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Use system audio|Try again/ })).not.toBeInTheDocument();
      expect(sessionStorage.getItem("hz.audio.nosystem")).toBe("1");
    } finally {
      sessionStorage.removeItem("hz.audio.nosystem");
      vi.unstubAllGlobals();
    }
  });

  it("after a declined system audio prompt shows one plain line and Try again, never the other choices", async () => {
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
      expect(screen.queryByRole("radiogroup", { name: "Listen to" })).not.toBeInTheDocument();
      expect(captureMicrophone).not.toHaveBeenCalled();
      fireEvent.click(pane().getByRole("button", { name: "Try again" }));
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("system audio"));
      expect(captureAudio).toHaveBeenCalledTimes(2);
      expect(vi.mocked(captureAudio).mock.calls.every((call) => call[1] === true)).toBe(true);
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

  it("fills the chosen segment of each choice, and only that one", () => {
    render(<AudioVisualizer />);
    open();
    const show = within(screen.getByRole("radiogroup", { name: "Show" }));
    expect(show.getByRole("radio", { name: "Both" })).toHaveAttribute("aria-checked", "true");
    expect(show.getByRole("radio", { name: "Both" })).toHaveClass("bg-primary", "text-primary-foreground");
    for (const name of ["Spectrum", "Aurora"]) {
      expect(show.getByRole("radio", { name })).toHaveAttribute("aria-checked", "false");
      expect(show.getByRole("radio", { name })).not.toHaveClass("bg-primary");
    }
    fireEvent.click(show.getByRole("radio", { name: "Aurora" }));
    expect(show.getByRole("radio", { name: "Aurora" })).toHaveAttribute("aria-checked", "true");
    expect(show.getByRole("radio", { name: "Both" })).toHaveAttribute("aria-checked", "false");
    expect(document.querySelectorAll("[aria-pressed]")).toHaveLength(0);
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
});
