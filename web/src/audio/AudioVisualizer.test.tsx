import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AudioVisualizer, spectrumLevel } from "./AudioVisualizer";
import { captureAudio, captureMicrophone } from "./capture";
import { publishOwnAudio } from "./own";
import { reloadSettings, setMaster, setStyle } from "./viz/settings";

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

afterEach(() => {
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
      fireEvent.click(screen.getByRole("button", { name: /Audio visualizer,/ }));
      fireEvent.click(screen.getByRole("button", { name: "Share audio" }));
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
      expect(screen.getByText("Listening to this tab. Waiting for sound.")).toBeInTheDocument();
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
    act(() => setStyle("spectrum", false));
    expect(screen.queryByRole("img", { name: "Shared audio spectrum" })).not.toBeInTheDocument();
    act(() => setStyle("spectrum", true));
    expect(screen.getByRole("img", { name: "Shared audio spectrum" })).toBeInTheDocument();
    act(() => setMaster(false));
    expect(screen.queryByRole("region", { name: "Audio visualizer" })).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Shared audio spectrum" })).not.toBeInTheDocument();
  });

  it("switches each style from the pane's pressed toggles", () => {
    render(<AudioVisualizer />);
    fireEvent.click(screen.getByRole("button", { name: /Audio visualizer,/ }));
    const controls = screen.getByRole("region", { name: "Audio visualizer controls" });
    const spectrum = within(controls).getByRole("button", { name: "Spectrum" });
    const aurora = within(controls).getByRole("button", { name: "Aurora" });
    expect(spectrum).toHaveAttribute("aria-pressed", "true");
    expect(aurora).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(spectrum);
    expect(spectrum).toHaveAttribute("aria-pressed", "false");
    expect(aurora).toHaveAttribute("aria-pressed", "true");
    expect(JSON.parse(localStorage.getItem("hz.audio.viz") ?? "{}").styles?.spectrum ?? false).toBe(false);
    fireEvent.click(spectrum);
    expect(spectrum).toHaveAttribute("aria-pressed", "true");
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
    const button = screen.getByRole("button", { name: /Audio visualizer,/ });
    expect(captureAudio).not.toHaveBeenCalled();
    fireEvent.click(button);
    const controls = screen.getByRole("region", { name: "Audio visualizer controls" });
    expect(within(controls).getByText(/does not record or upload audio or video/)).toBeVisible();
    expect(captureAudio).not.toHaveBeenCalled();
    fireEvent.click(within(controls).getByRole("button", { name: "Share audio" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Stop sharing audio" })).toBeInTheDocument());
    expect(captureAudio).toHaveBeenCalledOnce();
    view.unmount();
    expect(stop).toHaveBeenCalledOnce();
  });

  it("describes the workspace-only consent route when the desktop exposes it", () => {
    vi.stubGlobal("heinzelDesktop", { workspaceAudio: true });
    try {
      render(<AudioVisualizer />);
      fireEvent.click(screen.getByRole("button", { name: /Audio visualizer,/ }));
      expect(screen.getByRole("button", { name: "Share workspace audio" })).toBeVisible();
      expect(screen.getByText(/needs no permission/)).toBeVisible();
      expect(screen.queryByText(/Share tab audio/)).not.toBeInTheDocument();
      expect(captureAudio).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("reports permission failures and cancels an in-flight picker when disabled", async () => {
    vi.mocked(captureAudio).mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"));
    render(<AudioVisualizer />);
    fireEvent.click(screen.getByRole("button", { name: /Audio visualizer,/ }));
    fireEvent.click(screen.getByRole("button", { name: "Share audio" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("cancelled or blocked"));
    const status = screen.getByRole("status");
    expect(status).toBeVisible();
    expect(status).not.toHaveClass("sr-only");
    cleanup();

    vi.mocked(captureAudio).mockImplementationOnce((_signal) => new Promise(() => undefined));
    const { rerender } = render(<AudioVisualizer />);
    fireEvent.click(screen.getByRole("button", { name: /Audio visualizer,/ }));
    fireEvent.click(screen.getByRole("button", { name: "Share audio" }));
    const signal = vi.mocked(captureAudio).mock.calls.at(-1)?.[0];
    expect(signal?.aborted).toBe(false);
    rerender(<AudioVisualizer enabled={false} />);
    expect(signal?.aborted).toBe(true);
  });
});
