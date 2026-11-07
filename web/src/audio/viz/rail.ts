// Style 1, Spectrum: a ladder of 32 rounded pills over the rail's full height, bass at the bottom; each widens and
// glows with its band, peak caps fall slowly, and it is a dim still ladder when quiet.
import { rgba } from "../aurora";
import { type Analysis, clamp, VIZ_BANDS } from "./analysis";
import { mixRgb, type VizTheme } from "./theme";

export function railLayout(width: number, height: number) {
  const gap = 3;
  const pill = Math.max(1, (height - gap * (VIZ_BANDS + 1)) / VIZ_BANDS);
  return { gap, pill, minThick: 4, maxThick: Math.max(4, width - 2) };
}

function pillPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number) {
  const radius = Math.min(h, w) / 2;
  ctx.beginPath();
  if (typeof ctx.roundRect === "function") ctx.roundRect(x, y, w, h, radius);
  else ctx.rect(x, y, w, h);
}

/** `reduced`: a static calm variant, no glow and no beat lift. */
export function drawRail(
  ctx: CanvasRenderingContext2D,
  analysis: Analysis,
  theme: VizTheme,
  width: number,
  height: number,
  reduced: boolean,
) {
  ctx.clearRect(0, 0, width, height);
  const { gap, pill, minThick, maxThick } = railLayout(width, height);
  const lift = reduced ? 0 : analysis.beat * 0.15;
  const quiet = analysis.quiet;
  for (let band = 0; band < VIZ_BANDS; band += 1) {
    const value = quiet ? 0 : (analysis.smooth[band] ?? 0);
    const peak = quiet ? 0 : (analysis.peaks[band] ?? 0);
    const color = rgba(mixRgb(theme.a, theme.b, band / (VIZ_BANDS - 1)), 1);
    const thick = minThick + value * (maxThick - minThick);
    const top = height - (gap + band * (pill + gap)) - pill;
    ctx.globalAlpha = quiet ? 0.14 : clamp(0.2 + value * 0.8 + lift, 0, 1);
    ctx.fillStyle = color;
    if (!reduced && value > 0.35) {
      ctx.shadowBlur = 6 + value * 10;
      ctx.shadowColor = color;
    } else ctx.shadowBlur = 0;
    pillPath(ctx, (width - thick) / 2, top, thick, pill);
    ctx.fill();
    ctx.shadowBlur = 0;
    if (!quiet && peak > value + 0.04) {
      const cap = minThick + peak * (maxThick - minThick);
      ctx.globalAlpha = 0.55;
      pillPath(ctx, (width - cap) / 2, top, 2, pill);
      ctx.fill();
      pillPath(ctx, (width + cap) / 2 - 2, top, 2, pill);
      ctx.fill();
    }
  }
  ctx.globalAlpha = 1;
}
