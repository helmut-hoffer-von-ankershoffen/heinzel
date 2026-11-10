// Explicit, ephemeral browser capture. Audio never leaves the page and is never connected to speakers.
export type CapturedAudio = {
  analyser: AnalyserNode;
  stream: MediaStream;
  label: string;
  stop: () => void;
};

/** The desktop app's bridge: whether it can loop back system audio, and which source the next capture uses. */
type DesktopAudio = {
  systemAudio?: () => Promise<unknown>;
  audioSource?: (source: "workspace" | "system") => Promise<unknown>;
};
const desktopAudio = () => (globalThis as { heinzelDesktop?: DesktopAudio }).heinzelDesktop;

/** Whether this page runs in a desktop app that can answer `systemAudioAvailable` (the answer itself is async). */
export function hasDesktopAudio(): boolean {
  return typeof desktopAudio()?.systemAudio === "function";
}

/** Whether the desktop app offers system audio here; false in every browser and wherever Electron cannot loop back. */
export async function systemAudioAvailable(): Promise<boolean> {
  try {
    return (await desktopAudio()?.systemAudio?.()) === true;
  } catch {
    return false;
  }
}

/**
 * Ask the browser for a shared tab/display with audio; availability belongs to the browser and OS. In the desktop app
 * `system` loops back all sound the computer plays (its own consent, no picker); otherwise the workspace is shared.
 */
export async function captureAudio(signal: AbortSignal, system = false): Promise<CapturedAudio> {
  if (signal.aborted) throw new DOMException("Capture cancelled", "AbortError");
  if (!navigator.mediaDevices?.getDisplayMedia || !globalThis.AudioContext)
    // NotSupportedError, like a browser that has the call but cannot share: the pane then stops offering it.
    throw new DOMException(
      "Audio sharing is unavailable. Your browser or operating system may not support audio capture.",
      "NotSupportedError",
    );
  const context = new AudioContext();
  let stream: MediaStream | undefined;
  let source: MediaStreamAudioSourceNode | undefined;
  let analyser: AnalyserNode | undefined;
  let closed = false;
  const stoppedTracks = new Set<MediaStreamTrack>();
  const stopTrack = (track: MediaStreamTrack) => {
    if (stoppedTracks.has(track)) return;
    stoppedTracks.add(track);
    track.stop();
  };
  const stop = () => {
    signal.removeEventListener("abort", stop);
    for (const track of stream?.getTracks() ?? []) stopTrack(track);
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
    // Hints keep this tab (including its embedded player) available; the person still chooses the source.
    const options = {
      video: { displaySurface: "browser" },
      audio: true,
      systemAudio: "include",
      preferCurrentTab: true,
      selfBrowserSurface: "include",
    };
    // The desktop app arms the source of this one capture first; a refused system choice never falls back silently.
    const desktop = desktopAudio();
    if (desktop?.audioSource) {
      const armed = await desktop.audioSource(system ? "system" : "workspace");
      if (system && armed !== true)
        throw new Error("System audio is not available in this app. Choose another source.");
      if (signal.aborted || closed) throw new DOMException("Capture cancelled", "AbortError");
    }
    // Request the browser picker first in the initiating click, then resume analysis before activation expires.
    const sharing = navigator.mediaDevices.getDisplayMedia(options);
    const resumed = context.resume().then(
      () => true,
      () => false,
    );
    stream = await sharing;
    // The chooser requires video, but analysis needs only audio. Never retain screen imagery.
    for (const track of stream.getTracks()) if (track.kind === "video") stopTrack(track);
    if (signal.aborted || closed) throw new DOMException("Capture cancelled", "AbortError");
    if (!(await resumed)) throw new Error("Audio could not start. Stop sharing, then try again.");
    if (signal.aborted || closed) throw new DOMException("Capture cancelled", "AbortError");
    // A returned track may already have ended, including when the OS could not provide system audio.
    const tracks = stream.getAudioTracks().filter((track) => track.readyState === "live");
    if (!tracks.length)
      throw new Error(
        `No audio was shared. Your browser or operating system may not offer audio for this source.${system ? "" : " Choose a tab with Share audio, or stop sharing."}`,
      );
    source = context.createMediaStreamSource(new MediaStream(tracks));
    analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.75;
    source.connect(analyser);
    return {
      analyser,
      stream,
      label: system ? "System audio" : (tracks[0]?.label || "Shared audio").slice(0, 200),
      stop,
    };
  } catch (error) {
    stop(); // Also stops a stream returned after an earlier cancellation closed the AudioContext.
    throw error;
  }
}

/**
 * Listen with the microphone (already granted to the app, or asked once by the browser). It hears speakers and the
 * room, at lower fidelity than Heinzel's own sound. Processing is off so music is not flattened; nothing is recorded,
 * stored or uploaded, and the audio is never connected to the speakers.
 */
export async function captureMicrophone(signal: AbortSignal): Promise<CapturedAudio> {
  if (signal.aborted) throw new DOMException("Capture cancelled", "AbortError");
  if (!navigator.mediaDevices?.getUserMedia || !globalThis.AudioContext)
    throw new Error("The microphone is unavailable in this browser.");
  const context = new AudioContext();
  let stream: MediaStream | undefined;
  let source: MediaStreamAudioSourceNode | undefined;
  let closed = false;
  const stop = () => {
    signal.removeEventListener("abort", stop);
    for (const track of stream?.getTracks() ?? []) track.stop();
    stream = undefined;
    source?.disconnect();
    source = undefined;
    if (!closed) {
      closed = true;
      void context.close().catch(() => undefined);
    }
  };
  signal.addEventListener("abort", stop, { once: true });
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
    if (signal.aborted || closed) throw new DOMException("Capture cancelled", "AbortError");
    await context.resume();
    if (signal.aborted || closed) throw new DOMException("Capture cancelled", "AbortError");
    source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.75;
    source.connect(analyser);
    return { analyser, stream, label: "Microphone", stop };
  } catch (error) {
    stop();
    throw error;
  }
}
