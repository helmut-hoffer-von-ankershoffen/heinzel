// Explicit, ephemeral browser capture. Audio never leaves the page and is never connected to speakers.
export type CapturedAudio = {
  analyser: AnalyserNode;
  stream: MediaStream;
  label: string;
  stop: () => void;
};

/** Ask the browser for a shared tab/display with audio; availability belongs to the browser and OS. */
export async function captureAudio(signal: AbortSignal): Promise<CapturedAudio> {
  if (signal.aborted) throw new DOMException("Capture cancelled", "AbortError");
  if (!navigator.mediaDevices?.getDisplayMedia || !globalThis.AudioContext)
    throw new Error("Audio sharing is unavailable. Your browser or operating system may not support audio capture.");
  const context = new AudioContext();
  let stream: MediaStream | undefined;
  let source: MediaStreamAudioSourceNode | undefined;
  let analyser: AnalyserNode | undefined;
  let closed = false;
  const stop = () => {
    signal.removeEventListener("abort", stop);
    for (const track of stream?.getTracks() ?? []) track.stop();
    stream = undefined;
    source?.disconnect();
    source = undefined;
    analyser?.disconnect();
    analyser = undefined;
    if (!closed) {
      closed = true;
      void context.close().catch(() => undefined);
    }
  };
  signal.addEventListener("abort", stop, { once: true });
  try {
    const options = { video: true, audio: true, systemAudio: "include" };
    // Request the browser picker first in the initiating click, then resume analysis before activation expires.
    const sharing = navigator.mediaDevices.getDisplayMedia(options);
    const resumed = context.resume().then(
      () => true,
      () => false,
    );
    stream = await sharing;
    if (signal.aborted || closed) throw new DOMException("Capture cancelled", "AbortError");
    const tracks = stream.getAudioTracks();
    if (!tracks.length)
      throw new Error(
        "No audio was shared. Your browser or operating system may not offer audio for this source. Choose a tab with Share audio, or stop sharing.",
      );
    if (!(await resumed)) throw new Error("Audio could not start. Stop sharing, then try again.");
    if (signal.aborted || closed) throw new DOMException("Capture cancelled", "AbortError");
    source = context.createMediaStreamSource(new MediaStream(tracks));
    analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.75;
    source.connect(analyser);
    return { analyser, stream, label: (tracks[0]?.label || "Shared audio").slice(0, 200), stop };
  } catch (error) {
    stop(); // Also stops a stream returned after an earlier cancellation closed the AudioContext.
    throw error;
  }
}
