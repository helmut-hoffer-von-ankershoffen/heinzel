import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { captureAudio } from "./capture";

function track(kind: "audio" | "video") {
  return { kind, label: `${kind} source`, stop: vi.fn() };
}

const audio = track("audio");
const video = track("video");
const shared = {
  getTracks: () => [audio, video],
  getAudioTracks: () => [audio],
};
const analyser = { fftSize: 0, smoothingTimeConstant: 0, disconnect: vi.fn() };
const source = { connect: vi.fn(), disconnect: vi.fn() };
const context = {
  resume: vi.fn(),
  close: vi.fn(),
  createAnalyser: vi.fn(() => analyser),
  createMediaStreamSource: vi.fn((_stream: MediaStream) => source),
};
const choose = vi.fn();
const construct = vi.fn(function AudioContextMock() {
  return context;
});

beforeEach(() => {
  vi.clearAllMocks();
  context.resume.mockResolvedValue(undefined);
  context.close.mockResolvedValue(undefined);
  context.createMediaStreamSource.mockImplementation(() => source);
  choose.mockResolvedValue(shared);
  vi.stubGlobal("AudioContext", construct);
  vi.stubGlobal("navigator", { mediaDevices: { getDisplayMedia: choose } });
  vi.stubGlobal(
    "MediaStream",
    class {
      constructor(public tracks: unknown[]) {}
    },
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("explicit ephemeral audio capture", () => {
  it("asks only when called, analyses the shared audio without speaker output, and stops every track", async () => {
    expect(choose).not.toHaveBeenCalled();
    const captured = await captureAudio(new AbortController().signal);
    expect(choose).toHaveBeenCalledWith({ video: true, audio: true, systemAudio: "include" });
    expect(context.createMediaStreamSource.mock.calls[0]?.[0]).toEqual({ tracks: [audio] });
    expect(source.connect).toHaveBeenCalledExactlyOnceWith(analyser);
    expect(analyser.fftSize).toBe(1024);
    expect(captured.label).toBe("audio source");
    captured.stop();
    captured.stop();
    expect(audio.stop).toHaveBeenCalledOnce();
    expect(video.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
  });

  it("refuses a display with no audio and releases its video", async () => {
    choose.mockResolvedValue({ ...shared, getAudioTracks: () => [] });
    await expect(captureAudio(new AbortController().signal)).rejects.toThrow("No audio was shared");
    expect(video.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
  });

  it("does not open the picker after cancellation", async () => {
    const request = new AbortController();
    request.abort();
    await expect(captureAudio(request.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(choose).not.toHaveBeenCalled();
    expect(construct).not.toHaveBeenCalled();
  });

  it("closes a late shared stream after the UI was closed while the picker was open", async () => {
    let deliver!: (stream: unknown) => void;
    choose.mockReturnValue(new Promise((resolve) => (deliver = resolve)));
    const request = new AbortController();
    const pending = captureAudio(request.signal);
    request.abort();
    deliver(shared);
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(audio.stop).toHaveBeenCalledOnce();
    expect(video.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
    expect(source.connect).not.toHaveBeenCalled();
  });

  it("cleans up permission rejection without inventing signal data", async () => {
    choose.mockRejectedValue(new DOMException("Cancelled", "NotAllowedError"));
    await expect(captureAudio(new AbortController().signal)).rejects.toMatchObject({ name: "NotAllowedError" });
    expect(context.close).toHaveBeenCalledOnce();
    expect(source.connect).not.toHaveBeenCalled();
  });

  it("releases shared tracks if the AudioContext cannot start", async () => {
    context.resume.mockRejectedValue(new Error("not permitted"));
    await expect(captureAudio(new AbortController().signal)).rejects.toThrow("Audio could not start");
    expect(audio.stop).toHaveBeenCalledOnce();
    expect(video.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
  });

  it("cleans up after a source-node failure", async () => {
    context.createMediaStreamSource.mockImplementation(() => {
      throw new Error("source unavailable");
    });
    await expect(captureAudio(new AbortController().signal)).rejects.toThrow("source unavailable");
    expect(audio.stop).toHaveBeenCalledOnce();
    expect(video.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
  });

  it("explains an unsupported browser before asking for any permission", async () => {
    vi.stubGlobal("navigator", { mediaDevices: {} });
    await expect(captureAudio(new AbortController().signal)).rejects.toThrow("Audio sharing is unavailable");
    expect(choose).not.toHaveBeenCalled();
    expect(construct).not.toHaveBeenCalled();
  });
});
