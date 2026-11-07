import { AudioLines } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FooterItem, type State } from "@/components/FooterItem";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { BANDS, bandEdges, canvasSize, createState, drawAurora, isSettled, readPalette, stepState } from "./aurora";
import { type CapturedAudio, captureAudio } from "./capture";

export { spectrumLevel } from "./aurora";

function captureProblem(error: unknown) {
  if (error instanceof DOMException && error.name === "AbortError") return "Audio sharing was cancelled.";
  if (error instanceof DOMException && error.name === "NotAllowedError")
    return "Audio sharing was cancelled or blocked. Your browser or operating system may not offer audio for this source.";
  return error instanceof Error ? error.message : "Audio sharing is unavailable.";
}

/**
 * A real-spectrum aurora across the whole window, driven by the shared audio, with a slim control rail. Showing the
 * control never starts capture; the person clicks to share. Nothing is drawn, and no frame is scheduled, when the audio
 * is not shared or is silent.
 */
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
    if (!element || !drawing || !session) return;
    const data = new Uint8Array(session.analyser.frequencyBinCount);
    const edges = bandEdges(data.length, BANDS);
    const state = createState(Date.now() | 0);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let palette = readPalette();
    let frame = 0;
    let timer = 0;
    let last = 0;
    let lastSound = -Infinity;
    let audible = false;
    let size = { width: 0, height: 0 };
    let paletteAge = 0;
    const resize = () => {
      const width = Math.max(1, element.clientWidth);
      const height = Math.max(1, element.clientHeight);
      const next = canvasSize(width, height, window.devicePixelRatio);
      if (element.width !== next.width || element.height !== next.height) {
        element.width = next.width;
        element.height = next.height;
      }
      drawing.setTransform(next.width / width, 0, 0, next.height / height, 0, 0);
      size = { width, height };
    };
    const sample = (now: number) => {
      session.analyser.getByteFrequencyData(data);
      let sound = false;
      for (const value of data) {
        if (value > 0) {
          sound = true;
          break;
        }
      }
      if (sound) lastSound = now;
      const detected = now - lastSound < 2000;
      if (detected !== audible) {
        audible = detected;
        setHasSignal(detected);
      }
      return sound;
    };
    const sleep = () => {
      drawing.setTransform(1, 0, 0, 1, 0, 0);
      drawing.clearRect(0, 0, element.width, element.height);
      const poll = () => {
        timer = 0;
        if (document.visibilityState === "hidden") return;
        if (sample(performance.now())) {
          last = 0;
          frame = requestAnimationFrame(paint);
        } else timer = window.setTimeout(poll, 300);
      };
      timer = window.setTimeout(poll, 300);
    };
    const paint = (now: number) => {
      frame = 0;
      if (document.visibilityState === "hidden") return;
      const slow = reduced.matches;
      if (slow && now - last < 250) {
        frame = requestAnimationFrame(paint);
        return;
      }
      const dt = last ? Math.min(0.1, (now - last) / 1000) : 1 / 60;
      last = now;
      if (
        size.width !== Math.max(1, element.clientWidth) ||
        size.height !== Math.max(1, element.clientHeight) ||
        size.width === 0
      )
        resize();
      if (paletteAge++ % 120 === 0) palette = readPalette();
      sample(now);
      stepState(state, data, edges, dt, now, { reduced: slow, width: size.width, height: size.height });
      drawAurora(drawing, state, palette, size.width, size.height);
      if (isSettled(state) && now - lastSound > 1500) sleep();
      else frame = requestAnimationFrame(paint);
    };
    const visibility = () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(timer);
      frame = 0;
      timer = 0;
      last = 0;
      if (document.visibilityState !== "hidden") frame = requestAnimationFrame(paint);
    };
    document.addEventListener("visibilitychange", visibility);
    visibility();
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(timer);
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
      <div className="min-h-0 flex-1" aria-hidden />
      {session &&
        createPortal(
          <canvas
            ref={canvas}
            aria-hidden
            data-testid="audio-aurora"
            className="pointer-events-none fixed inset-0 z-[5] size-full"
          />,
          document.body,
        )}
    </div>
  );
}
