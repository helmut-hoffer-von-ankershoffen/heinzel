import { AudioLines, BarChart3, Mic, MonitorSpeaker, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FooterItem, type State } from "@/components/FooterItem";
import { PaneAction } from "@/components/PaneActions";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import {
  BANDS,
  bandEdges,
  canvasSize,
  createState,
  drawAurora,
  FRAME_MS,
  isSettled,
  readPalette,
  stepState,
} from "./aurora";
import { type CapturedAudio, captureAudio, captureMicrophone } from "./capture";
import { useOwnAudio } from "./own";
import { Analysis, bandRanges } from "./viz/analysis";
import { drawRail } from "./viz/rail";
import { setMaster, setStyle, styleOn, useVizSettings } from "./viz/settings";
import { readVizTheme } from "./viz/theme";

export { spectrumLevel } from "./aurora";

type Remembered = "tab" | "mic" | null;
const SOURCE_KEY = "hz.audio.source";

function readSource(): Remembered {
  try {
    const saved = localStorage.getItem(SOURCE_KEY);
    return saved === "tab" || saved === "mic" ? saved : null;
  } catch {
    return null;
  }
}

function captureProblem(error: unknown) {
  if (error instanceof DOMException && error.name === "AbortError") return "Audio sharing was cancelled.";
  if (error instanceof DOMException && error.name === "NotAllowedError")
    return "Audio sharing was cancelled or blocked. Your browser or operating system may not offer audio for this source.";
  return error instanceof Error ? error.message : "Audio sharing is unavailable.";
}

/**
 * The audio visualizations: one analyser and one render loop feed every style that is switched on. Heinzel's own sound
 * is analysed in the page with no prompt; music in a host's frame (YouTube) needs the person's consent, asked once,
 * inline, as a tab share or the microphone. Nothing is captured before a click (a microphone already allowed may be
 * resumed), and nothing is scheduled while the sound is silent, the page hidden or every style off.
 */
/** Why the capture actions keep their words: each starts a browser capture that asks for permission. */
const CAPTURE = "Starting a tab or microphone capture is consequential: the browser asks for permission.";

export function AudioVisualizer({ enabled = true }: { readonly enabled?: boolean }) {
  const own = useOwnAudio();
  const settings = useVizSettings();
  const [remembered, setRemembered] = useState<Remembered>(readSource);
  const [session, setSession] = useState<CapturedAudio | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [problem, setProblem] = useState("");
  const [hasSignal, setHasSignal] = useState(false);
  const current = useRef<CapturedAudio | null>(null);
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const auroraCanvas = useRef<HTMLCanvasElement>(null);
  const railCanvas = useRef<HTMLCanvasElement>(null);
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
  const start = useCallback(async (kind: "mic" | "tab") => {
    const revision = ++generation.current;
    const controller = new AbortController();
    request.current = controller;
    setRequesting(true);
    setProblem("");
    try {
      const captured = await (kind === "mic" ? captureMicrophone : captureAudio)(controller.signal);
      if (generation.current !== revision || controller.signal.aborted) {
        captured.stop();
        return;
      }
      current.current = captured;
      setSession(captured);
      setRequesting(false);
      setRemembered(kind);
      try {
        localStorage.setItem(SOURCE_KEY, kind);
      } catch {
        // the choice just isn't remembered
      }
    } catch (error) {
      if (generation.current !== revision) return;
      setRequesting(false);
      setProblem(captureProblem(error));
    }
  }, []);
  useEffect(() => {
    if (!session) return;
    const tracks = session.stream.getTracks();
    for (const track of tracks) track.addEventListener("ended", stop, { once: true });
    if (session.stream.getAudioTracks().every((track) => track.readyState === "ended")) stop();
    return () => {
      for (const track of tracks) track.removeEventListener("ended", stop);
    };
  }, [session, stop]);
  // A microphone the person already allowed and chose before listens again without another question.
  const framed = own.mode === "frame";
  useEffect(() => {
    if (!enabled || !framed || session || requesting || remembered !== "mic") return;
    let cancelled = false;
    void navigator.permissions
      ?.query({ name: "microphone" as PermissionName })
      .then((status) => {
        if (!cancelled && status.state === "granted") void start("mic");
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [enabled, framed, session, requesting, remembered, start]);
  const ownAnalyser = own.mode === "analysed" ? own.analyser : null;
  const analyser = session?.analyser ?? ownAnalyser;
  const microphone = session?.label === "Microphone";
  const railOn = styleOn(settings, "spectrum");
  const auroraOn = styleOn(settings, "aurora");
  const drawing = Boolean(analyser) && (railOn || auroraOn);
  useEffect(() => {
    if (!analyser || (!railOn && !auroraOn)) return;
    const rail = railOn ? railCanvas.current : null;
    const sky = auroraOn ? auroraCanvas.current : null;
    const railContext = rail?.getContext("2d") ?? null;
    const skyContext = sky?.getContext("2d") ?? null;
    if (!railContext && !skyContext) return;
    const freq = new Uint8Array(analyser.frequencyBinCount);
    const time = typeof analyser.getByteTimeDomainData === "function" ? new Uint8Array(analyser.fftSize || 2048) : null;
    const sampleRate = analyser.context?.sampleRate ?? 48000;
    const analysis = new Analysis(bandRanges(sampleRate, freq.length * 2, freq.length));
    const edges = bandEdges(freq.length, BANDS);
    const aurora = createState(Date.now() | 0);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let palette = readPalette();
    let theme = readVizTheme();
    let frame = 0;
    let timer = 0;
    let last = 0;
    let lastSound = -Infinity;
    let audible = false;
    let railSize = { width: 0, height: 0 };
    let skySize = { width: 0, height: 0 };
    let age = 0;
    const sizeOf = (element: HTMLCanvasElement, context: CanvasRenderingContext2D) => {
      const width = Math.max(1, element.clientWidth);
      const height = Math.max(1, element.clientHeight);
      const next = canvasSize(width, height, window.devicePixelRatio);
      if (element.width !== next.width || element.height !== next.height) {
        element.width = next.width;
        element.height = next.height;
      }
      context.setTransform(next.width / width, 0, 0, next.height / height, 0, 0);
      return { width, height };
    };
    const sample = (now: number, dt: number) => {
      analyser.getByteFrequencyData(freq);
      if (time) analyser.getByteTimeDomainData(time);
      let sound = false;
      for (const value of freq) {
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
      analysis.step(freq, time, now, dt);
      return sound;
    };
    const sleep = () => {
      // After two quiet seconds the aurora clears and the rail rests as a dim, still ladder; the loop stops.
      // The loop can settle before sample()'s detection window ends, so report the silence here: otherwise the
      // status would keep saying "Visualizing" while nothing is drawn.
      if (audible) {
        audible = false;
        setHasSignal(false);
      }
      analysis.rest();
      if (rail && railContext) {
        railSize = sizeOf(rail, railContext);
        drawRail(railContext, analysis, theme, railSize.width, railSize.height, true);
      }
      if (sky && skyContext) {
        skyContext.setTransform(1, 0, 0, 1, 0, 0);
        skyContext.clearRect(0, 0, sky.width, sky.height);
      }
      const poll = () => {
        timer = 0;
        if (document.visibilityState === "hidden") return;
        analyser.getByteFrequencyData(freq);
        if (freq.some((value) => value > 0)) {
          last = 0;
          lastSound = performance.now();
          frame = requestAnimationFrame(paint);
        } else timer = window.setTimeout(poll, 300);
      };
      timer = window.setTimeout(poll, 300);
    };
    const paint = (now: number) => {
      frame = 0;
      if (document.visibilityState === "hidden") return;
      const slow = reduced.matches;
      if (now - last < (slow ? 250 : FRAME_MS - 1)) {
        frame = requestAnimationFrame(paint);
        return;
      }
      const dt = last ? Math.min(0.1, (now - last) / 1000) : 1 / 60;
      last = now;
      if (age++ % 120 === 0) {
        palette = readPalette();
        theme = readVizTheme();
      }
      sample(now, dt);
      if (rail && railContext) {
        if (railSize.width !== Math.max(1, rail.clientWidth) || railSize.height !== Math.max(1, rail.clientHeight))
          railSize = sizeOf(rail, railContext);
        drawRail(railContext, analysis, theme, railSize.width, railSize.height, slow);
      }
      if (sky && skyContext) {
        if (skySize.width !== Math.max(1, sky.clientWidth) || skySize.height !== Math.max(1, sky.clientHeight))
          skySize = sizeOf(sky, skyContext);
        stepState(aurora, freq, edges, dt, now, {
          reduced: slow,
          floor: microphone,
          width: skySize.width,
          height: skySize.height,
        });
        drawAurora(skyContext, aurora, palette, skySize.width, skySize.height);
      }
      const settled = !sky || isSettled(aurora);
      if (settled && analysis.quiet && now - lastSound > 1500) sleep();
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
  }, [analyser, microphone, railOn, auroraOn]);
  if (!enabled || !settings.master) return null;
  const workspaceAudio =
    (window as Window & { heinzelDesktop?: { workspaceAudio?: boolean } }).heinzelDesktop?.workspaceAudio === true;
  let status = "Nothing to visualize yet. Press Play on a sound, or share a tab.";
  let state: State = "off";
  let triggerLabel = "Audio visualizer";
  let controlWord = "Audio";
  if (requesting) {
    status = "Waiting for the browser to allow audio.";
    triggerLabel = "Audio request";
    controlWord = "Wait";
  } else if (session) {
    const what = microphone ? "the microphone" : "this tab";
    status = hasSignal ? `Visualizing ${what}` : `Listening to ${what}. Waiting for sound.`;
    state = hasSignal ? "ok" : "warn";
    triggerLabel = "Audio sharing active";
    controlWord = "On";
  } else if (ownAnalyser) {
    status = hasSignal
      ? `Visualizing ${own.mode === "analysed" ? own.label : "Heinzel's sound"}`
      : "Heinzel's sound is playing quietly.";
    state = hasSignal ? "ok" : "warn";
    triggerLabel = "Audio sharing active";
    controlWord = "On";
  } else if (framed) {
    status = "This sound plays in a frame the page cannot analyse. Share this tab or use the microphone to see it.";
    state = "warn";
    controlWord = "Share";
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
        actions={
          <>
            <PaneAction
              icon={MonitorSpeaker}
              primary={remembered === "tab"}
              withText={CAPTURE}
              title="Exact signal. The browser shows a sharing bar."
              onClick={() => (session || requesting ? stop() : void start("tab"))}
            >
              {session && !microphone ? "Stop sharing audio" : workspaceAudio ? "Share workspace audio" : "Share audio"}
            </PaneAction>
            <PaneAction
              icon={Mic}
              primary={remembered === "mic"}
              withText={CAPTURE}
              title="Uses your microphone; nothing is recorded or uploaded. No sharing bar, lower fidelity."
              onClick={() => (session || requesting ? stop() : void start("mic"))}
            >
              {session && microphone ? "Stop listening" : "Use microphone"}
            </PaneAction>
          </>
        }
      >
        <div className="space-y-3 px-1.5 pb-1">
          <output className={problem ? "block break-words text-bad" : "block break-words text-foreground"}>
            {status}
          </output>
          <fieldset className="flex flex-wrap gap-x-4 gap-y-1 border-0 p-0 text-xs">
            <legend className="mb-1">Show</legend>
            <label className="flex min-h-8 items-center gap-2">
              <input
                type="checkbox"
                checked={settings.styles.spectrum}
                onChange={(event) => setStyle("spectrum", event.target.checked)}
              />
              <BarChart3 aria-hidden className="size-4" />
              Spectrum
            </label>
            <label className="flex min-h-8 items-center gap-2">
              <input
                type="checkbox"
                checked={settings.styles.aurora}
                onChange={(event) => setStyle("aurora", event.target.checked)}
              />
              <Sparkles aria-hidden className="size-4" />
              Aurora
            </label>
          </fieldset>
          <p className="text-xs text-muted-foreground">
            Heinzel's own sound needs no permission. Sharing a tab shows your browser's sharing bar; the microphone
            hears speakers and the room.
          </p>
          <p className="text-xs text-muted-foreground">{t("audio.visualizerHelp")}</p>
          <label className="flex min-h-8 items-center gap-2 text-xs">
            <input type="checkbox" checked={settings.master} onChange={(event) => setMaster(event.target.checked)} />
            Audio visualisation
          </label>
        </div>
      </FooterItem>
      {railOn && analyser ? (
        <canvas
          ref={railCanvas}
          role="img"
          aria-label="Shared audio spectrum"
          className="pointer-events-none min-h-0 w-full flex-1 px-0.5 pb-1"
        />
      ) : (
        <div className="min-h-0 flex-1" aria-hidden />
      )}
      {drawing &&
        auroraOn &&
        createPortal(
          <canvas
            ref={auroraCanvas}
            aria-hidden
            data-testid="audio-aurora"
            className="pointer-events-none fixed inset-0 -z-10 size-full"
          />,
          document.body,
        )}
      {framed && !session && !requesting && (
        <SourcePrompt remembered={remembered} onTab={() => void start("tab")} onMic={() => void start("mic")} />
      )}
    </div>
  );
}

/** The one compact question, once music plays in a frame: browsers require the person's own yes. On a phone or a short
 * landscape screen a floating card would cover the player it asks about, so there the question stays in the Audio
 * pane (its trigger reads "Share", with Share audio and Use microphone). */
function SourcePrompt({ remembered, onTab, onMic }: { remembered: Remembered; onTab: () => void; onMic: () => void }) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;
  return createPortal(
    <aside
      aria-label="Let Heinzel see the music"
      className="fixed right-12 bottom-14 z-(--layer-popover) w-64 max-sm:hidden [@media(max-height:40rem)]:hidden rounded-xl border border-border bg-popover p-3 text-xs text-foreground shadow-lg"
    >
      <p className="mb-2 font-medium">Let Heinzel see the music?</p>
      <div className="flex gap-2">
        <Button size="sm" variant={remembered === "mic" ? "outline" : "default"} onClick={onTab}>
          Share this tab
        </Button>
        <Button size="sm" variant={remembered === "mic" ? "default" : "outline"} onClick={onMic}>
          Use microphone
        </Button>
        <Button size="sm" variant="ghost" aria-label="Not now" onClick={() => setDismissed(true)}>
          Not now
        </Button>
      </div>
    </aside>,
    document.body,
  );
}
