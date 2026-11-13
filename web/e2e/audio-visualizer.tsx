// Test transport only: the production capture graph receives real generated audio, never microphone/system audio.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import type { SchemaConfigReport } from "../src/api/client";
import { AudioVisualizer } from "../src/audio/AudioVisualizer";
import { Sidebar } from "../src/layout/Sidebar";
import { PlayerCard } from "../src/sound/PlayerCard";
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
      if (fixture.mode === "music") playMusic(context, gain);
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
/**
 * A generated band for the screenshots: a kick twice a second, a held chord and a hi-hat, so every style has bass,
 * mids, treble and beats to show. Synthesised in the page; nothing is captured.
 */
function playMusic(context: AudioContext, out: GainNode) {
  const start = context.currentTime + 0.05;
  const voice = (frequency: number, level: number, type: OscillatorType = "sine") => {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = type;
    oscillator.frequency.value = frequency;
    gain.gain.value = level;
    oscillator.connect(gain).connect(out);
    oscillator.start();
    return gain;
  };
  const kick = voice(58, 0);
  const hat = voice(9200, 0, "square");
  for (const [frequency, level] of [
    [220, 0.25],
    [277, 0.2],
    [330, 0.2],
    [660, 0.12],
    [1320, 0.08],
    [2640, 0.05],
  ] as const)
    voice(frequency, level, "triangle");
  for (let beat = 0; beat < 240; beat += 1) {
    const at = start + beat * 0.5;
    kick.gain.setValueAtTime(1.6, at);
    kick.gain.exponentialRampToValueAtTime(0.001, at + 0.28);
    hat.gain.setValueAtTime(0.12, at + 0.25);
    hat.gain.exponentialRampToValueAtTime(0.001, at + 0.33);
  }
}

const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
// Seed only the fictional setting needed by the real Sidebar. All unseeded queries stay disabled, and the
// browser test refuses unexpected network requests, including any request to the local fixture's API.
const client = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false } } });
const config: SchemaConfigReport = {
  files: [],
  can_reveal: false,
  file_manager: "Fixture",
  settings: [
    {
      section: "hz",
      key: "playful",
      value: true,
      default: true,
      source: "default",
      env: "HZ_PLAYFUL",
      description: "Fictional fixture setting",
    },
  ],
};
client.setQueryData(["system", "config", "show"], config);
createRoot(root).render(
  // Like the app shell: the workspace paints no background of its own (the page colour is on <html>), so the aurora
  // under the content shows through it.
  <QueryClientProvider client={client}>
    <MemoryRouter>
      <div className="flex h-dvh text-foreground">
        <aside aria-label="Main" className="h-full w-64 shrink-0 border-r border-border bg-sidebar">
          <Sidebar collapsed={false} onToggle={() => {}} onSearch={() => {}} onNotifications={() => {}} />
        </aside>
        <div className="min-w-0 flex-1 p-4">
          <h1>Generated test audio</h1>
          <p>No microphone, display or system audio is captured.</p>
        </div>
        <PlayerCard title="Fictional player" watchUrl="https://example.invalid/watch" host="Fixture">
          <div>Fictional player content</div>
        </PlayerCard>
        <section aria-label="Audio visualizer" className="min-h-0 w-11 shrink-0 self-stretch border-l border-border">
          <AudioVisualizer />
        </section>
      </div>
    </MemoryRouter>
  </QueryClientProvider>,
);
