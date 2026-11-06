// Test transport only: the production capture graph receives real generated audio, never microphone/system audio.
import { createRoot } from "react-dom/client";
import { AudioVisualizer } from "../src/audio/AudioVisualizer";
import "../src/index.css";

const fixture = {
  mode: "tone",
  requests: 0,
  streams: [] as MediaStream[],
  contexts: [] as AudioContext[],
  oscillator: null as OscillatorNode | null,
  gain: null as GainNode | null,
  options: null as object | null,
};
Object.assign(window, { audioFixture: fixture });
Object.defineProperty(navigator, "mediaDevices", {
  configurable: true,
  value: {
    getDisplayMedia: async (options: object) => {
      fixture.requests += 1;
      fixture.options = options;
      if (fixture.mode === "deny") throw new DOMException("Fixture permission rejection", "NotAllowedError");
      const video = document.createElement("canvas").captureStream(1);
      const context = new AudioContext();
      fixture.contexts.push(context);
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      const destination = context.createMediaStreamDestination();
      oscillator.frequency.value = 440;
      gain.gain.value = 0.4;
      oscillator.connect(gain).connect(destination);
      oscillator.start();
      await context.resume();
      fixture.oscillator = oscillator;
      fixture.gain = gain;
      const stream = new MediaStream([
        ...video.getVideoTracks(),
        ...(fixture.mode === "no-audio" ? [] : destination.stream.getAudioTracks()),
      ]);
      fixture.streams.push(stream);
      return stream;
    },
  },
});
const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
createRoot(root).render(
  <div className="flex h-dvh bg-background text-foreground">
    <div className="min-w-0 flex-1 p-4">
      <h1>Generated test audio</h1>
      <p>No microphone, display or system audio is captured.</p>
    </div>
    <section aria-label="Audio visualizer" className="min-h-0 w-11 shrink-0 self-stretch border-l border-border">
      <AudioVisualizer />
    </section>
  </div>,
);
