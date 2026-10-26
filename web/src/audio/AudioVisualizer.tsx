import { useCallback, useEffect, useRef, useState } from "react";
import { type CapturedAudio, captureAudio } from "./capture";

/** A real spectrum at the right edge. Showing the control never starts capture; the person clicks to share. */
export function AudioVisualizer({ enabled = true }: { enabled?: boolean }) {
  const [session, setSession] = useState<CapturedAudio | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [problem, setProblem] = useState("");
  const current = useRef<CapturedAudio | null>(null);
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const canvas = useRef<HTMLCanvasElement>(null);
  const stop = useCallback(() => {
    generation.current += 1;
    request.current?.abort();
    request.current = null;
    current.current?.stop();
    current.current = null;
    setSession(null);
    setRequesting(false);
  }, []);
  useEffect(() => {
    if (!enabled) stop();
  }, [enabled, stop]);
  useEffect(
    () => () => {
      generation.current += 1;
      request.current?.abort();
      current.current?.stop();
    },
    [],
  );
  const start = async () => {
    const revision = ++generation.current;
    const controller = new AbortController();
    request.current = controller;
    setRequesting(true);
    setProblem("");
    try {
      const captured = await captureAudio(controller.signal);
      if (generation.current !== revision || controller.signal.aborted) {
        captured.stop();
        return;
      }
      current.current = captured;
      setSession(captured);
      setRequesting(false);
    } catch (error) {
      if (generation.current !== revision) return;
      setRequesting(false);
      setProblem(
        error instanceof DOMException && error.name === "AbortError"
          ? "Audio sharing was cancelled."
          : error instanceof DOMException && error.name === "NotAllowedError"
            ? "Audio sharing was cancelled or blocked. Your browser or operating system may not offer audio for this source."
            : error instanceof Error
              ? error.message
              : "Audio sharing is unavailable.",
      );
    }
  };
  useEffect(() => {
    if (!session) return;
    const tracks = session.stream.getTracks();
    for (const track of tracks) track.addEventListener("ended", stop, { once: true });
    if (session.stream.getAudioTracks().every((track) => track.readyState === "ended")) stop();
    return () => {
      for (const track of tracks) track.removeEventListener("ended", stop);
    };
  }, [session, stop]);
  useEffect(() => {
    const element = canvas.current;
    const drawing = element?.getContext("2d");
    if (!element || !drawing) return;
    if (!session) {
      drawing.clearRect(0, 0, element.width, element.height);
      return;
    }
    const data = new Uint8Array(session.analyser.frequencyBinCount);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let last = -Infinity;
    const paint = (now: number) => {
      if (document.visibilityState === "hidden") return;
      frame = requestAnimationFrame(paint);
      if (now - last < (reduced.matches ? 250 : 1000 / 30)) return;
      last = now;
      const width = Math.max(1, element.clientWidth);
      const height = Math.max(1, element.clientHeight);
      const ratio = Math.min(2, window.devicePixelRatio || 1);
      if (element.width !== Math.round(width * ratio) || element.height !== Math.round(height * ratio)) {
        element.width = Math.round(width * ratio);
        element.height = Math.round(height * ratio);
      }
      drawing.setTransform(ratio, 0, 0, ratio, 0, 0);
      drawing.clearRect(0, 0, width, height);
      drawing.fillStyle = getComputedStyle(element).color;
      session.analyser.getByteFrequencyData(data);
      const rows = Math.max(8, Math.min(128, Math.floor(height / 8)));
      for (let row = 0; row < rows; row += 1) {
        const from = Math.floor(((rows - 1 - row) * data.length) / rows);
        const to = Math.max(from + 1, Math.floor(((rows - row) * data.length) / rows));
        let peak = 0;
        for (let index = from; index < to; index += 1) peak = Math.max(peak, data[index] ?? 0);
        const bar = 1 + (peak / 255) * (width - 1);
        drawing.globalAlpha = 0.2 + (peak / 255) * 0.8;
        drawing.fillRect((width - bar) / 2, (row * height) / rows, bar, 2);
      }
      drawing.globalAlpha = 1;
    };
    const visibility = () => {
      cancelAnimationFrame(frame);
      if (document.visibilityState !== "hidden") frame = requestAnimationFrame(paint);
    };
    document.addEventListener("visibilitychange", visibility);
    visibility();
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [session]);
  if (!enabled) return null;
  const label = requesting
    ? "Cancel audio capture request"
    : session
      ? `Stop audio visualization: ${session.label}`
      : "Start audio visualization";
  return (
    <button
      type="button"
      className="relative flex h-dvh min-h-6 w-6 shrink-0 items-stretch justify-center px-1 text-muted-foreground hover:text-foreground"
      aria-label={label}
      aria-pressed={Boolean(session)}
      aria-describedby="audio-visualizer-help"
      title={
        problem ||
        `${label}. You choose the browser source. Audio stays in this tab and is not recorded or uploaded. Browser or OS audio sharing may be unavailable.`
      }
      onClick={() => (session || requesting ? stop() : void start())}
    >
      <canvas ref={canvas} aria-hidden className="h-full w-full" />
      <span id="audio-visualizer-help" className="sr-only">
        You choose the browser source. Audio stays in this tab and is not recorded or uploaded. Browser or operating
        system audio sharing may be unavailable.
      </span>
      {!session && <span aria-hidden className="absolute inset-x-1 top-1/2 h-0.5 bg-current opacity-40" />}
      <span role="status" className="sr-only">
        {problem ||
          (requesting ? "Choose a source and share its audio." : session ? `Listening to ${session.label}` : "")}
      </span>
    </button>
  );
}
