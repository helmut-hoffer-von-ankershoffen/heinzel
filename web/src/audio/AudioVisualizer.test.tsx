import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AudioVisualizer, spectrumGeometry, spectrumLevel } from "./AudioVisualizer";
import { captureAudio } from "./capture";

vi.mock("./capture", () => ({ captureAudio: vi.fn() }));

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

afterEach(() => vi.clearAllMocks());

describe("AudioVisualizer", () => {
  it("scales the visible spectrum to one third of its available height", () => {
    for (const height of [600, 900, 1440]) {
      expect(spectrumGeometry(28, height).maxBarHeight).toBe(height / 3);
    }
  });

  it("keeps the spectrum within the visible strip, even when it is narrow or short", () => {
    const sizes: [number, number][] = [
      [1, 1],
      [28, 24],
      [28, 320],
    ];
    for (const [width, height] of sizes) {
      const geometry = spectrumGeometry(width, height);
      expect(geometry.columns * geometry.columnWidth + geometry.gap * (geometry.columns - 1)).toBeLessThanOrEqual(
        width + 1,
      );
      expect(geometry.maxBarHeight).toBeLessThanOrEqual(Math.max(1, height / 3));
    }
  });

  it("draws full-scale audio bins up to one third of the actual canvas height", async () => {
    vi.mocked(captureAudio).mockResolvedValue({
      ...captured,
      analyser: {
        frequencyBinCount: 32,
        getByteFrequencyData: (data: Uint8Array) => data.fill(255),
      },
    } as unknown as Awaited<ReturnType<typeof captureAudio>>);
    const draw = { clearRect: vi.fn(), setTransform: vi.fn(), fillRect: vi.fn(), globalAlpha: 1 };
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue(draw as unknown as CanvasRenderingContext2D);
    const oldWidth = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, "clientWidth");
    const oldHeight = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, "clientHeight");
    const oldVisibility = Object.getOwnPropertyDescriptor(document, "visibilityState");
    Object.defineProperty(HTMLCanvasElement.prototype, "clientWidth", { configurable: true, value: 28 });
    Object.defineProperty(HTMLCanvasElement.prototype, "clientHeight", { configurable: true, value: 900 });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    const frames: FrameRequestCallback[] = [];
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    try {
      render(<AudioVisualizer />);
      fireEvent.click(screen.getByRole("button", { name: "Start audio visualization" }));
      await waitFor(() => expect(screen.getByRole("button", { name: /Stop audio visualization/ })).toBeInTheDocument());
      act(() => frames.shift()?.(0));
      const heights = draw.fillRect.mock.calls.map((call) => Number(call[3]));
      expect(heights.length).toBeGreaterThan(0);
      expect(Math.max(...heights)).toBe(300);
      expect(heights.every((height) => height <= 300)).toBe(true);
    } finally {
      raf.mockRestore();
      getContext.mockRestore();
      if (oldWidth) Object.defineProperty(HTMLCanvasElement.prototype, "clientWidth", oldWidth);
      else delete (HTMLCanvasElement.prototype as { clientWidth?: number }).clientWidth;
      if (oldHeight) Object.defineProperty(HTMLCanvasElement.prototype, "clientHeight", oldHeight);
      else delete (HTMLCanvasElement.prototype as { clientHeight?: number }).clientHeight;
      if (oldVisibility) Object.defineProperty(document, "visibilityState", oldVisibility);
      else delete (document as { visibilityState?: DocumentVisibilityState }).visibilityState;
    }
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
    const button = screen.getByRole("button", { name: "Start audio visualization" });
    expect(captureAudio).not.toHaveBeenCalled();
    expect(button.title).toContain("not recorded or uploaded");
    fireEvent.click(button);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Stop audio visualization: Selected browser tab" }),
      ).toBeInTheDocument(),
    );
    expect(captureAudio).toHaveBeenCalledOnce();
    view.unmount();
    expect(stop).toHaveBeenCalledOnce();
  });

  it("reports permission failures and cancels an in-flight picker when disabled", async () => {
    vi.mocked(captureAudio).mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"));
    render(<AudioVisualizer />);
    fireEvent.click(screen.getByRole("button", { name: "Start audio visualization" }));
    expect(await screen.findByRole("status")).toHaveTextContent("cancelled or blocked");
    cleanup();

    vi.mocked(captureAudio).mockImplementationOnce((_signal) => new Promise(() => undefined));
    const { rerender } = render(<AudioVisualizer />);
    fireEvent.click(screen.getByRole("button", { name: "Start audio visualization" }));
    const signal = vi.mocked(captureAudio).mock.calls.at(-1)?.[0];
    expect(signal?.aborted).toBe(false);
    rerender(<AudioVisualizer enabled={false} />);
    expect(signal?.aborted).toBe(true);
  });
});
