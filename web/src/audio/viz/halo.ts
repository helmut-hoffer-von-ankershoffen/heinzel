// Style 2, Halo: a radial spectrum around one Heinzel listening to the music. 48 rays grow with their band (bass at the
// bottom, treble at the top, mirrored left and right) and a thin ring breathes with the beat. Quiet: a still, dim ring.
// The beat is a scale change of at most 8 %, never a luminance flash; reduced motion shows the level and nothing pulses.
import { rgba } from "../aurora";
import { type Analysis, VIZ_BANDS } from "./analysis";
import { mixRgb, type VizTheme } from "./theme";

export const HALO_RAYS = 48;

/** The band a ray shows: bass at the bottom (ray 0), treble at the top (ray 24), mirrored on both sides. */
export function haloBand(ray: number) {
  const around = (ray % HALO_RAYS) / HALO_RAYS;
  return Math.round(Math.abs(((around * 2) % 2) - 1) * (VIZ_BANDS - 1));
}

/** Draws one frame and returns the scale for the Heinzel in the middle (1 at rest, at most 1.05 on a beat). */
export function drawHalo(
  ctx: CanvasRenderingContext2D,
  analysis: Analysis,
  theme: VizTheme,
  width: number,
  height: number,
  reduced: boolean,
) {
  ctx.clearRect(0, 0, width, height);
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.26;
  const quiet = analysis.quiet;
  const pulse = reduced || quiet ? 0 : analysis.beat * 0.08;
  ctx.lineCap = "round";
  ctx.lineWidth = Math.max(1.5, radius * 0.07);
  for (let ray = 0; ray < HALO_RAYS; ray += 1) {
    const angle = Math.PI / 2 + (ray / HALO_RAYS) * Math.PI * 2;
    const band = haloBand(ray);
    const value = quiet ? 0 : (analysis.smooth[band] ?? 0);
    const inner = radius * (1 + pulse) + radius * 0.12;
    const length = radius * 0.06 + value * radius * 0.75;
    ctx.strokeStyle = rgba(mixRgb(theme.a, theme.b, band / (VIZ_BANDS - 1)), 1);
    ctx.globalAlpha = quiet ? 0.18 : 0.35 + value * 0.65;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(angle) * inner, cy + Math.sin(angle) * inner);
    ctx.lineTo(cx + Math.cos(angle) * (inner + length), cy + Math.sin(angle) * (inner + length));
    ctx.stroke();
  }
  ctx.globalAlpha = quiet ? 0.35 : 0.6;
  ctx.lineWidth = Math.max(1, radius * 0.05);
  ctx.strokeStyle = rgba(theme.a, 1);
  ctx.beginPath();
  ctx.arc(cx, cy, radius * (1 + pulse), 0, Math.PI * 2);
  ctx.stroke();
  ctx.globalAlpha = 1;
  return 1 + (reduced || quiet ? 0 : analysis.beat * 0.05);
}
