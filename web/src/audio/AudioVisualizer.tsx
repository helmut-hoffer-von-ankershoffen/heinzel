import { AudioLines, BarChart3, Layers, Mic, MicOff, MonitorSpeaker, Sparkles } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FooterItem, type State } from "@/components/FooterItem";
import { Button } from "@/components/ui/button";
import { type Segment, SegmentedControl } from "@/components/ui/segmented";
import { t } from "@/i18n";
import { Switch } from "@/layout/SettingControls";
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
import { watchTheme } from "./viz/color";
import { drawRail } from "./viz/rail";
import { setMaster, setShown, shownOf, styleOn, useVizSettings, type VizShown } from "./viz/settings";
import { readVizTheme } from "./viz/theme";

export { spectrumLevel } from "./aurora";

/** What the person last chose to listen to; "off" is remembered too, so nothing resumes until they pick a source. */
type Source = "off" | "mic" | "tab";
type Remembered = Source | null;
const SOURCE_KEY = "hz.audio.source";

function readSource(): Remembered {
  try {
    const saved = localStorage.getItem(SOURCE_KEY);
    return saved === "tab" || saved === "mic" || saved === "off" ? saved : null;
  } catch {
    return null;
  }
}

function remember(source: Source) {
  try {
    localStorage.setItem(SOURCE_KEY, source);
  } catch {
    // the choice just isn't remembered
  }
}

const FELL_BACK = "Shared audio is not offered by this browser, so Heinzel listens with the microphone.";
const UNSHARED_KEY = "hz.audio.unshared";

/**
 * Whether this browser can share a tab's audio: feature detection, never the user agent. Every iPhone and iPad browser
 * (Safari, Chrome, Firefox) is WebKit without getDisplayMedia; a browser that has it but refused it as unsupported is
 * remembered for the session, so the choice is not offered again.
 */
export function canShareAudio(): boolean {
  if (typeof navigator.mediaDevices?.getDisplayMedia !== "function") return false;
  try {
    return sessionStorage.getItem(UNSHARED_KEY) !== "1";
  } catch {
    return true;
  }
}

function markUnshared() {
  try {
    sessionStorage.setItem(UNSHARED_KEY, "1");
  } catch {
    // not remembered: the next try fails the same way and falls back again
  }
}

/** The browser cannot share at all, as opposed to the person cancelling or refusing. */
const unsupported = (error: unknown) =>
  (error instanceof DOMException && error.name === "NotSupportedError") || error instanceof TypeError;

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
export function AudioVisualizer({ enabled = true }: { readonly enabled?: boolean }) {
  const own = useOwnAudio();
  const settings = useVizSettings();
  const [remembered, setRemembered] = useState<Remembered>(readSource);
  const [session, setSession] = useState<CapturedAudio | null>(null);
  const [requesting, setRequesting] = useState<"mic" | "tab" | null>(null);
  const [problem, setProblem] = useState("");
  const [sharing, setSharing] = useState(canShareAudio);
  const [notice, setNotice] = useState("");
  const [hasSignal, setHasSignal] = useState(false);
  const current = useRef<CapturedAudio | null>(null);
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const resumed = useRef(false);
  const auroraCanvas = useRef<HTMLCanvasElement>(null);
  const railCanvas = useRef<HTMLCanvasElement>(null);
  const masterId = useId();
  const stop = useCallback(() => {
    generation.current += 1;
    request.current?.abort();
    request.current = null;
    current.current?.stop();
    current.current = null;
    setSession(null);
    setHasSignal(false);
    setRequesting(null);
  }, []);
  useEffect(() => {
    if (!enabled || !settings.master) stop();
  }, [enabled, settings.master, stop]);
  useEffect(
    () => () => {
      generation.current += 1;
      request.current?.abort();
      current.current?.stop();
    },
    [],
  );
  /**
   * Listen to `kind`, switching live: the new source is asked for first, and the one running is stopped only once the
   * new one is granted, so a cancelled or refused prompt leaves the old source playing.
   */
  const start = useCallback(async (kind: "mic" | "tab") => {
    const revision = ++generation.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setRequesting(kind);
    setProblem("");
    try {
      const captured = await (kind === "mic" ? captureMicrophone : captureAudio)(controller.signal);
      if (generation.current !== revision || controller.signal.aborted) {
        captured.stop();
        return;
      }
      const previous = current.current;
      request.current = null;
      if (kind === "mic") resumed.current = true;
      current.current = captured;
      setSession(captured);
      setHasSignal(false);
      previous?.stop();
      setRequesting(null);
      setRemembered(kind);
      remember(kind);
    } catch (error) {
      if (generation.current !== revision) return;
      setRequesting(null);
      if (kind === "tab" && unsupported(error)) {
        // Offered but not supported here: stop offering it and listen with the microphone instead, in one line.
        markUnshared();
        setSharing(false);
        setNotice(FELL_BACK);
        if (!current.current) void start("mic");
        return;
      }
      setProblem(captureProblem(error));
    }
  }, []);
  const choose = useCallback(
    (source: Source) => {
      if (source === "off") {
        stop();
        setProblem("");
        setRemembered("off");
        remember("off");
        return;
      }
      if (source === "tab" && !canShareAudio()) {
        setSharing(false);
        setNotice("Shared audio is not offered by this browser.");
        return;
      }
      setNotice("");
      void start(source);
    },
    [start, stop],
  );
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
  // Once per page: a stopped or ended microphone never restarts itself.
  useEffect(() => {
    if (!enabled || !settings.master || !framed || session || requesting || remembered !== "mic" || resumed.current)
      return;
    let cancelled = false;
    void navigator.permissions
      ?.query({ name: "microphone" as PermissionName })
      .then((status) => {
        if (cancelled || status.state !== "granted") return;
        resumed.current = true;
        void start("mic");
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [enabled, settings.master, framed, session, requesting, remembered, start]);
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
    let resting = false;
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
    // The measured spectrum position, on the drawing's own element: the browser test asserts the real signal path
    // with it, and its failure log can tell a dead analyser from a drawing problem.
    const probe = rail ?? sky;
    if (probe) probe.dataset.vizSampleRate = String(sampleRate);
    const publish = () => {
      if (!probe) return;
      const centre = analysis.centroid().toFixed(3);
      if (probe.dataset.vizCentroid !== centre) probe.dataset.vizCentroid = centre;
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
      publish();
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
      resting = true;
      analysis.rest();
      publish();
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
      resting = false;
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
    // Colours follow the theme live: light/dark, a plugin theme or its daypart variant re-colour the next frame, and
    // the resting rail is redrawn at once.
    const unwatch = watchTheme(() => {
      palette = readPalette();
      theme = readVizTheme();
      if (resting && rail && railContext) drawRail(railContext, analysis, theme, railSize.width, railSize.height, true);
    });
    document.addEventListener("visibilitychange", visibility);
    visibility();
    return () => {
      unwatch();
      cancelAnimationFrame(frame);
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [analyser, microphone, railOn, auroraOn]);
  if (!enabled) return null;
  const workspaceAudio =
    (window as Window & { heinzelDesktop?: { workspaceAudio?: boolean } }).heinzelDesktop?.workspaceAudio === true;
  const sharedWord = workspaceAudio ? "Workspace audio" : "Shared audio";
  const listening: Source = session ? (microphone ? "mic" : "tab") : "off";
  let status = "Off. Heinzel's own sound still shows.";
  let state: State = "off";
  let triggerLabel = "Audio visualizer";
  let controlWord = "Audio";
  if (!settings.master) {
    status = "Audio visualisation is off.";
    controlWord = "Off";
  } else if (requesting) {
    status = "Waiting for the browser to allow audio.";
    triggerLabel = "Audio request";
    controlWord = "Wait";
  } else if (session) {
    const what = microphone ? "the microphone" : sharedWord.toLowerCase();
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
    status = sharing
      ? "Off. Music in a player frame shows once you pick Microphone or Shared audio."
      : "Off. Music in a player frame shows once you pick Microphone.";
    state = "warn";
    controlWord = "Share";
  }
  if (problem && settings.master) {
    status = problem;
    state = "bad";
    triggerLabel = "Audio sharing problem";
    controlWord = "Retry";
  }
  const shown: VizShown | null = shownOf(settings);
  const showSegments: Segment<VizShown>[] = [
    { value: "spectrum", label: "Spectrum", icon: BarChart3 },
    { value: "aurora", label: "Aurora", icon: Sparkles },
    { value: "both", label: "Both", icon: Layers },
  ];
  const sourceSegments: Segment<Source>[] = [
    { value: "off", label: "Off", icon: MicOff },
    { value: "mic", label: "Microphone", icon: Mic },
    ...(sharing ? [{ value: "tab" as const, label: sharedWord, icon: MonitorSpeaker }] : []),
  ];
  const mark = { ok: "text-ok", warn: "text-warn", bad: "text-bad", off: "text-muted-foreground" }[state] ?? "";
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
        className={`h-12 min-w-11 flex-col gap-0.5 px-1 ${
          state === "bad" ? "text-bad" : settings.master ? "text-foreground" : "text-muted-foreground"
        }`}
        popoverClassName="w-80 max-w-[calc(100vw-1rem)] p-3"
      >
        <div className="space-y-3 px-1.5 pb-1">
          {settings.master && (
            <>
              <SegmentedControl legend="Show" segments={showSegments} value={shown} onSelect={setShown} selectOnArrow />
              <SegmentedControl
                legend="Listen to"
                segments={sourceSegments}
                value={listening}
                busy={requesting}
                onSelect={choose}
              />
            </>
          )}
          <p className="flex items-start gap-1.5 text-sm">
            <span aria-hidden className={mark}>
              ●
            </span>
            <output
              aria-live="polite"
              className={state === "bad" ? "break-words text-bad" : "break-words text-foreground"}
            >
              {status}
            </output>
          </p>
          {notice && settings.master && <p className="text-xs text-foreground">{notice}</p>}
          <p className="text-xs text-muted-foreground">
            {t(sharing ? "audio.visualizerHelp" : "audio.visualizerHelpMicrophone")}
          </p>
          <div className="flex min-h-11 items-center justify-between gap-3 text-sm text-foreground">
            <span id={masterId}>Audio visualisation</span>
            <Switch
              labelledBy={masterId}
              on={settings.master}
              disabled={false}
              onToggle={() => setMaster(!settings.master)}
            />
          </div>
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
      {settings.master && framed && !session && !requesting && remembered !== "off" && (
        <SourcePrompt
          remembered={remembered}
          onTab={sharing ? () => choose("tab") : null}
          onMic={() => choose("mic")}
        />
      )}
    </div>
  );
}

/** The one compact question, once music plays in a frame: browsers require the person's own yes. On a phone or a short
 * landscape screen a floating card would cover the player it asks about, so there the question stays in the Audio
 * pane (its trigger reads "Share", with Share audio and Use microphone). */
function SourcePrompt({
  remembered,
  onTab,
  onMic,
}: {
  remembered: Remembered;
  /** Null where the browser cannot share a tab's audio (every iPhone browser): only the microphone is offered. */
  onTab: (() => void) | null;
  onMic: () => void;
}) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;
  return createPortal(
    <aside
      aria-label="Let Heinzel see the music"
      className="fixed right-12 bottom-14 z-(--layer-popover) w-64 max-sm:hidden [@media(max-height:40rem)]:hidden rounded-xl border border-border bg-popover p-3 text-xs text-foreground shadow-lg"
    >
      <p className="mb-2 font-medium">Let Heinzel see the music?</p>
      <div className="flex gap-2">
        {onTab && (
          <Button size="sm" variant={remembered === "mic" ? "outline" : "default"} onClick={onTab}>
            Share this tab
          </Button>
        )}
        <Button size="sm" variant={remembered === "mic" || !onTab ? "default" : "outline"} onClick={onMic}>
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
