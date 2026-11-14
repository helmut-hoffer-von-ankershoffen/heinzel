import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { captureAudio, captureMicrophone, systemAudioAvailable } from "./capture";

function track(kind: "audio" | "video") {
  return { kind, label: `${kind} source`, readyState: "live", stop: vi.fn() };
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
    expect(choose).toHaveBeenCalledWith({
      video: { displaySurface: "browser" },
      audio: true,
      systemAudio: "include",
      preferCurrentTab: true,
      selfBrowserSurface: "include",
    });
    expect(context.createMediaStreamSource.mock.calls[0]?.[0]).toEqual({ tracks: [audio] });
    expect(source.connect).toHaveBeenCalledExactlyOnceWith(analyser);
    expect(analyser.fftSize).toBe(1024);
    expect(captured.label).toBe("audio source");
    expect(video.stop).toHaveBeenCalledOnce();
    expect(audio.stop).not.toHaveBeenCalled();
    captured.stop();
    captured.stop();
    expect(audio.stop).toHaveBeenCalledOnce();
    expect(video.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
  });

  it("refuses a display with no audio and releases its video", async () => {
    context.resume.mockReturnValue(new Promise<void>(() => {}));
    choose.mockResolvedValue({ ...shared, getAudioTracks: () => [] });
    await expect(captureAudio(new AbortController().signal)).rejects.toThrow("No audio was shared");
    expect(video.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
  });

  it("rejects and releases an audio track that ends before activation", async () => {
    vi.stubGlobal("heinzelDesktop", { audioSource: vi.fn().mockResolvedValue(true) });
    let resume!: () => void;
    context.resume.mockReturnValue(new Promise<void>((resolve) => (resume = resolve)));
    const ended = track("audio");
    choose.mockResolvedValue({ getTracks: () => [ended, video], getAudioTracks: () => [ended] });
    const pending = captureAudio(new AbortController().signal, true);
    await Promise.resolve();
    ended.readyState = "ended";
    resume();
    await expect(pending).rejects.toThrow("No audio was shared");
    expect(ended.stop).toHaveBeenCalledOnce();
    expect(video.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
    expect(source.connect).not.toHaveBeenCalled();
  });

  it("rejects an already-ended audio track without waiting for activation", async () => {
    vi.stubGlobal("heinzelDesktop", { audioSource: vi.fn().mockResolvedValue(true) });
    context.resume.mockReturnValue(new Promise<void>(() => {}));
    const ended = { ...track("audio"), readyState: "ended" };
    choose.mockResolvedValue({ getTracks: () => [ended, video], getAudioTracks: () => [ended] });
    await expect(captureAudio(new AbortController().signal, true)).rejects.toThrow("No audio was shared");
    expect(ended.stop).toHaveBeenCalledOnce();
    expect(video.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
    expect(source.connect).not.toHaveBeenCalled();
  });

  it("stops video before waiting for audio activation", async () => {
    let resume!: () => void;
    context.resume.mockReturnValue(new Promise<void>((resolve) => (resume = resolve)));
    const pending = captureAudio(new AbortController().signal);
    await vi.waitFor(() => expect(video.stop).toHaveBeenCalledOnce());
    expect(audio.stop).not.toHaveBeenCalled();
    expect(source.connect).not.toHaveBeenCalled();
    resume();
    const captured = await pending;
    captured.stop();
    expect(video.stop).toHaveBeenCalledOnce();
    expect(audio.stop).toHaveBeenCalledOnce();
  });

  it("settles cancellation and stops audio when activation never completes", async () => {
    context.resume.mockReturnValue(new Promise<void>(() => {}));
    const controller = new AbortController();
    const pending = captureAudio(controller.signal);
    await vi.waitFor(() => expect(video.stop).toHaveBeenCalledOnce());
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(audio.stop).toHaveBeenCalledOnce();
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

describe("captureMicrophone", () => {
  it("asks with processing off, feeds only an analyser and releases the microphone on stop", async () => {
    const mic = track("audio");
    const stream = { getTracks: () => [mic], getAudioTracks: () => [mic] };
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    vi.stubGlobal("AudioContext", construct);
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    const captured = await captureMicrophone(new AbortController().signal);
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
    expect(captured.label).toBe("Microphone");
    expect(source.connect).toHaveBeenCalledWith(analyser);
    expect(source.connect).not.toHaveBeenCalledWith(expect.objectContaining({ destination: expect.anything() }));
    captured.stop();
    expect(mic.stop).toHaveBeenCalled();
    expect(context.close).toHaveBeenCalled();
  });

  it("settles Stop while permission is pending and releases a later microphone grant", async () => {
    const mic = track("audio");
    let resolve!: (value: { getTracks: () => (typeof mic)[] }) => void;
    const permission = new Promise<{ getTracks: () => (typeof mic)[] }>((done) => {
      resolve = done;
    });
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: () => permission } });
    const controller = new AbortController();
    const pending = captureMicrophone(controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    resolve({ getTracks: () => [mic] });
    await permission;
    expect(mic.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
  });

  it("is cancelled before asking when already aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(captureMicrophone(controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("desktop system audio", () => {
  it("is offered only when the desktop app reports it", async () => {
    expect(await systemAudioAvailable()).toBe(false);
    vi.stubGlobal("heinzelDesktop", { systemAudio: vi.fn().mockResolvedValue(true) });
    expect(await systemAudioAvailable()).toBe(true);
    vi.stubGlobal("heinzelDesktop", { systemAudio: vi.fn().mockRejectedValue(new Error("gone")) });
    expect(await systemAudioAvailable()).toBe(false);
    vi.stubGlobal("heinzelDesktop", { systemAudio: vi.fn().mockResolvedValue("yes") });
    expect(await systemAudioAvailable()).toBe(false);
  });

  it.each([
    [true, 48_000],
    [44_100, 44_100],
  ])(
    "feeds native system samples from start response %s at %i Hz and releases capture on stop",
    async (start, rate) => {
      const pcm = new Float32Array([0.25, -0.25]);
      const nativeAudio = vi.fn((action: string) =>
        Promise.resolve(action === "read" ? btoa(String.fromCharCode(...new Uint8Array(pcm.buffer))) : start),
      );
      const copyToChannel = vi.fn();
      const play = { buffer: null, connect: vi.fn(), start: vi.fn() };
      const output = track("audio");
      const createBuffer = vi.fn(() => ({ copyToChannel, duration: pcm.length / rate }));
      vi.stubGlobal("heinzelDesktop", { nativeAudio });
      vi.stubGlobal(
        "AudioContext",
        vi.fn(function nativeContext() {
          return {
            ...context,
            currentTime: 0,
            createAnalyser: () => ({ ...analyser, connect: vi.fn() }),
            createMediaStreamDestination: () => ({ stream: { getTracks: () => [output] } }),
            createBuffer,
            createBufferSource: () => play,
          };
        }),
      );

      const captured = await captureAudio(new AbortController().signal, true);
      await vi.waitFor(() => expect(copyToChannel).toHaveBeenCalledWith(pcm, 0));
      expect(createBuffer).toHaveBeenCalledWith(1, pcm.length, rate);
      expect(play.connect).toHaveBeenCalledWith(captured.analyser);
      expect(choose).not.toHaveBeenCalled();
      captured.stop();
      expect(nativeAudio).toHaveBeenLastCalledWith("stop");
      expect(output.stop).toHaveBeenCalledOnce();
    },
  );

  it("settles Stop even if the native picker does not respond", async () => {
    let resolve!: (value: boolean) => void;
    const picker = new Promise<boolean>((done) => {
      resolve = done;
    });
    const nativeAudio = vi.fn((action: string) => (action === "start" ? picker : Promise.resolve(true)));
    const output = track("audio");
    vi.stubGlobal("heinzelDesktop", { nativeAudio });
    vi.stubGlobal("AudioContext", function nativeContext() {
      return {
        ...context,
        createAnalyser: () => ({ ...analyser, connect: vi.fn() }),
        createMediaStreamDestination: () => ({ stream: { getTracks: () => [output] } }),
      };
    });
    const controller = new AbortController();
    const pending = captureAudio(controller.signal, true);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    resolve(true);
    await picker;
    expect(nativeAudio.mock.calls).toEqual([["start"], ["stop"]]);
    expect(output.stop).toHaveBeenCalledOnce();
  });

  it("arms the chosen source before the one display request", async () => {
    const audioSource = vi.fn().mockResolvedValue(true);
    vi.stubGlobal("heinzelDesktop", { audioSource });
    const captured = await captureAudio(new AbortController().signal, true);
    expect(audioSource).toHaveBeenCalledExactlyOnceWith("system");
    expect(audioSource.mock.invocationCallOrder[0]).toBeLessThan(choose.mock.invocationCallOrder[0] ?? 0);
    expect(captured.label).toBe("System audio");
    captured.stop();
    await captureAudio(new AbortController().signal);
    expect(audioSource).toHaveBeenLastCalledWith("workspace");
  });

  it("never falls back to another source when the app refuses system audio", async () => {
    vi.stubGlobal("heinzelDesktop", { audioSource: vi.fn().mockResolvedValue(false) });
    await expect(captureAudio(new AbortController().signal, true)).rejects.toThrow("System audio is not available");
    expect(choose).not.toHaveBeenCalled();
    expect(context.close).toHaveBeenCalledOnce();
  });

  it("settles cancellation without opening a picker if desktop source selection never responds", async () => {
    vi.stubGlobal("heinzelDesktop", { audioSource: vi.fn().mockReturnValue(new Promise(() => {})) });
    const controller = new AbortController();
    const pending = captureAudio(controller.signal, true);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(choose).not.toHaveBeenCalled();
    expect(context.close).toHaveBeenCalledOnce();
  });

  it("stops every late system track when cancelled while the request is pending", async () => {
    vi.stubGlobal("heinzelDesktop", { audioSource: vi.fn().mockResolvedValue(true) });
    let deliver!: (stream: unknown) => void;
    choose.mockReturnValue(new Promise((resolve) => (deliver = resolve)));
    const controller = new AbortController();
    const pending = captureAudio(controller.signal, true);
    await vi.waitFor(() => expect(choose).toHaveBeenCalledOnce());
    controller.abort();
    deliver(shared);
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(audio.stop).toHaveBeenCalledOnce();
    expect(video.stop).toHaveBeenCalledOnce();
    expect(context.close).toHaveBeenCalledOnce();
    expect(source.connect).not.toHaveBeenCalled();
  });

  it("settles cancellation before the native picker returns and stops any later stream", async () => {
    vi.stubGlobal("heinzelDesktop", { audioSource: vi.fn().mockResolvedValue(true) });
    let deliver!: (stream: unknown) => void;
    choose.mockReturnValue(new Promise((resolve) => (deliver = resolve)));
    const controller = new AbortController();
    const pending = captureAudio(controller.signal, true);
    await vi.waitFor(() => expect(choose).toHaveBeenCalledOnce());
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(context.close).toHaveBeenCalledOnce();
    deliver(shared);
    await vi.waitFor(() => expect(audio.stop).toHaveBeenCalledOnce());
    expect(video.stop).toHaveBeenCalledOnce();
    expect(source.connect).not.toHaveBeenCalled();
  });

  it("does not silently request a different source if the desktop bridge is missing", async () => {
    await expect(captureAudio(new AbortController().signal, true)).rejects.toThrow("System audio is not available");
    expect(choose).not.toHaveBeenCalled();
    expect(context.close).toHaveBeenCalledOnce();
  });
});
