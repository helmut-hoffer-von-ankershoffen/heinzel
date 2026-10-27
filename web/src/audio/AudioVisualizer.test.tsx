import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  it("uses most of the available height for spectrum marks on tall displays", () => {
    for (const height of [600, 900, 1440]) {
      const { rows, barHeight } = spectrumGeometry(height);
      expect((rows * barHeight) / height).toBeGreaterThanOrEqual(0.67);
    }
  });

  it("keeps spectrum marks inside the visualizer at short heights", () => {
    for (const height of [24, 320]) {
      const { rows, rowHeight, barHeight } = spectrumGeometry(height);
      expect(rows * rowHeight).toBeLessThanOrEqual(height);
      expect(barHeight).toBeLessThanOrEqual(rowHeight);
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
