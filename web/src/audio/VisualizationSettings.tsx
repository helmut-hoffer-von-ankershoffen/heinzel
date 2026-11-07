import { BarChart3, CircleDot, Sparkles, Users } from "lucide-react";
import { setMaster, setStyle, useVizSettings, type VizStyle } from "./viz/settings";

const ROWS = [
  {
    style: "spectrum",
    label: "Spectrum",
    help: "Bars in the right rail that rise and glow with the music.",
    Icon: BarChart3,
  },
  { style: "aurora", label: "Aurora", help: "Soft ribbons of light under the whole window.", Icon: Sparkles },
  {
    style: "halo",
    label: "Halo",
    help: "A ring of rays around one Heinzel above the player, growing with each band.",
    Icon: CircleDot,
  },
  {
    style: "swarm",
    label: "Swarm",
    help: "Three Heinzels above the player jump, sway and nod with the music.",
    Icon: Users,
  },
] as const satisfies readonly { style: VizStyle; label: string; help: string; Icon: typeof BarChart3 }[];

/** Settings > Appearance: the master switch and one switch per style, all on by default and applied at once. */
export function VisualizationSettings() {
  const settings = useVizSettings();
  return (
    <section aria-labelledby="audio-viz-settings">
      <h3 id="audio-viz-settings" className="mb-1 text-ui font-medium">
        Audio visualisation
      </h3>
      <p className="mb-2 text-sm text-muted-foreground">
        Heinzel shows how its music sounds. Nothing is recorded or uploaded; music in a frame, such as YouTube, is seen
        only after you share the tab or use the microphone.
      </p>
      <label className="flex min-h-9 items-center gap-2 text-sm">
        <input type="checkbox" checked={settings.master} onChange={(event) => setMaster(event.target.checked)} />
        Audio visualisation
      </label>
      <div className="ml-6 border-l border-border/70 pl-3">
        {ROWS.map(({ style, label, help, Icon }) => (
          <label key={style} className="flex min-h-9 items-start gap-2 py-1 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={settings.styles[style]}
              disabled={!settings.master}
              onChange={(event) => setStyle(style, event.target.checked)}
            />
            <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
            <span>
              {label}
              <span className="block text-xs text-muted-foreground">{help}</span>
            </span>
          </label>
        ))}
      </div>
    </section>
  );
}
