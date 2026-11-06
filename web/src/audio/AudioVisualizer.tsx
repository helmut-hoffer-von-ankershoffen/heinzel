import { AudioLines } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { FooterItem, type State } from "@/components/FooterItem";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { type CapturedAudio, captureAudio } from "./capture";

export function spectrumGeometry(width: number, height: number) {
  const columns = Math.max(1, Math.min(16, Math.floor(width / 3)));
  const gap = width >= columns * 2 ? 1 : 0;
  const columnWidth = Math.max(1, (width - gap * (columns - 1)) / columns);
  return { columns, gap, columnWidth, maxBarHeight: Math.max(1, height / 3) };
}

export function spectrumLevel(value: number) {
  return Math.sqrt(Math.max(0, Math.min(255, value)) / 255);
}

function captureProblem(error: unknown) {
  if (error instanceof DOMException && error.name === "AbortError") return "Audio sharing was cancelled.";
  if (error instanceof DOMException && error.name === "NotAllowedError")
    return "Audio sharing was cancelled or blocked. Your browser or operating system may not offer audio for this source.";
  return error instanceof Error ? error.message : "Audio sharing is unavailable.";
}

/** A real spectrum at the right edge. Showing the control never starts capture; the person clicks to share. */
export function AudioVisualizer({ enabled = true }: { readonly enabled?: boolean }) {
  const [session, setSession] = useState<CapturedAudio | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [problem, setProblem] = useState("");
  const [hasSignal, setHasSignal] = useState(false);
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
    setHasSignal(false);
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
      setProblem(captureProblem(error));
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
    let lastSound = -Infinity;
    let audible = false;
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
      if (data.some((value) => value > 0)) lastSound = now;
      const detected = now - lastSound < 2000;
      if (detected !== audible) {
        audible = detected;
        setHasSignal(detected);
      }
      const { columns, gap, columnWidth, maxBarHeight } = spectrumGeometry(width, height);
      for (let column = 0; column < columns; column += 1) {
        const from = Math.floor((column * data.length) / columns);
        const to = Math.max(from + 1, Math.floor(((column + 1) * data.length) / columns));
        let peak = 0;
        for (let index = from; index < to; index += 1) peak = Math.max(peak, data[index] ?? 0);
        const level = spectrumLevel(peak);
        const bar = level * maxBarHeight;
        if (bar <= 0) continue;
        drawing.globalAlpha = 0.2 + level * 0.8;
        drawing.fillRect(column * (columnWidth + gap), height - bar, columnWidth, bar);
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
  const workspaceAudio =
    (window as Window & { heinzelDesktop?: { workspaceAudio?: boolean } }).heinzelDesktop?.workspaceAudio === true;
  let status = "Audio is not being shared.";
  let state: State = "off";
  let triggerLabel = "Audio visualizer";
  let controlWord = "Audio";
  let action = workspaceAudio ? "Share workspace audio" : "Share audio";
  if (requesting) {
    status = workspaceAudio
      ? "Confirm audio sharing in the desktop dialog."
      : "Choose a source and turn on Share audio in the browser picker.";
    triggerLabel = "Audio sharing request";
    controlWord = "Wait";
    action = "Cancel audio request";
  } else if (session) {
    status = hasSignal
      ? `Visualizing ${session.label}`
      : `Audio shared from ${session.label}. Waiting for sound: play audio in that source.`;
    state = hasSignal ? "ok" : "warn";
    triggerLabel = "Audio sharing active";
    controlWord = "On";
    action = "Stop sharing audio";
  }
  if (problem) {
    status = problem;
    state = "bad";
    triggerLabel = "Audio sharing problem";
    controlWord = "Retry";
  }
  return (
    <div className="flex h-full min-h-0 flex-col items-center text-muted-foreground">
      <FooterItem
        label="Audio visualizer"
        paneName="Audio visualizer controls"
        triggerLabel={triggerLabel}
        state={state}
        content={
          <>
            <AudioLines aria-hidden className="size-5" />
            <span className="text-[10px]">{controlWord}</span>
          </>
        }
        className={`h-12 min-w-11 flex-col gap-0.5 px-1 ${state === "bad" ? "text-bad" : "text-foreground"}`}
        popoverClassName="w-80 max-w-[calc(100vw-1rem)] p-3"
      >
        <div className="space-y-3 px-1.5 pb-1">
          <output className={problem ? "block break-words text-bad" : "block break-words text-foreground"}>
            {status}
          </output>
          {!session && !requesting && (
            <p className="text-xs text-muted-foreground">
              {workspaceAudio
                ? "Play audio in this workspace, then share workspace audio to visualize it."
                : "Play audio, then share the tab playing it. For the embedded player, choose this app’s tab and turn on Share tab audio."}
            </p>
          )}
          <Button className="min-h-11 w-full" onClick={() => (session || requesting ? stop() : void start())}>
            {action}
          </Button>
          <p className="text-xs text-muted-foreground">{t("audio.visualizerHelp")}</p>
          {!workspaceAudio && (
            <p className="text-xs text-muted-foreground">
              No audio option? Open hz in a Chrome browser tab and try tab sharing. Audio availability depends on your
              browser and operating system.
            </p>
          )}
        </div>
      </FooterItem>
      <canvas ref={canvas} role="img" aria-label="Shared audio spectrum" className="min-h-0 w-full flex-1 px-1" />
    </div>
  );
}
