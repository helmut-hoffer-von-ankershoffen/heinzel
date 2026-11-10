// Style 5, Heinzel swarm: three Heinzels stand above the player and work the music together. The first jumps on the
// bass, the second sways with the mids, the third nods to the treble, and lantern sparks drift up on the beats. Jumps are
// at most 14 px and sways 7 degrees; at most one spark burst per beat (beats are at most three a second). Quiet or under
// reduced motion they stand still and only the lantern glow follows the level.
import { rgba } from "../aurora";
import type { Analysis } from "./analysis";
import type { VizTheme } from "./theme";

export const SWARM_FIGURES = 3;
const MAX_SPARKS = 40;

export type Spark = { x: number; y: number; vx: number; vy: number; life: number; warm: boolean };
export type SwarmState = { jump: number; time: number; sparks: Spark[]; seed: number };

export const createSwarm = (seed = 1): SwarmState => ({ jump: 0, time: 0, sparks: [], seed: seed >>> 0 || 1 });

function random(state: SwarmState) {
  state.seed = (state.seed * 1664525 + 1013904223) >>> 0;
  return state.seed / 2 ** 32;
}

/** One CSS transform per figure, for this frame. */
export function stepSwarm(state: SwarmState, analysis: Analysis, dt: number, reduced: boolean): string[] {
  state.time += dt;
  const still = reduced || analysis.quiet;
  state.jump = still ? 0 : analysis.beatEvent ? 1 : Math.max(0, state.jump - dt / 0.42);
  if (still) return Array.from({ length: SWARM_FIGURES }, () => "none");
  const { bass, mid, treble } = analysis;
  const jump = Math.sin(Math.min(1, state.jump) * Math.PI) * 14 * Math.min(1, 0.5 + bass);
  const sway = Math.sin(state.time * 2.3) * Math.min(1, mid) * 7;
  const nod = Math.sin(state.time * 1.15) * Math.min(1, treble) * 4;
  return [
    `translateY(${(-jump - bass * 3).toFixed(2)}px) scale(${(1 + bass * 0.04).toFixed(3)}, ${(1 - state.jump * 0.06 + bass * 0.03).toFixed(3)})`,
    `translateY(${(-mid * 5).toFixed(2)}px) rotate(${sway.toFixed(2)}deg)`,
    `translateY(${(-treble * 3).toFixed(2)}px) rotate(${nod.toFixed(2)}deg) scale(${(1 + treble * 0.03).toFixed(3)})`,
  ];
}

/** Lantern glow at each figure's feet and the sparks; `centres` are the figures' x positions in CSS pixels. */
export function drawSwarm(
  ctx: CanvasRenderingContext2D,
  state: SwarmState,
  analysis: Analysis,
  theme: VizTheme,
  width: number,
  height: number,
  centres: readonly number[],
  dt: number,
  reduced: boolean,
) {
  ctx.clearRect(0, 0, width, height);
  const glow = analysis.quiet ? 0.06 : 0.1 + Math.min(1, analysis.level) * 0.3;
  for (const x of centres) {
    const light = ctx.createRadialGradient(x, height, 2, x, height, 30);
    light.addColorStop(0, rgba(theme.c, glow));
    light.addColorStop(1, rgba(theme.c, 0));
    ctx.fillStyle = light;
    ctx.fillRect(x - 32, height - 32, 64, 32);
  }
  if (!reduced && !analysis.quiet && analysis.beatEvent)
    for (const x of centres)
      for (let index = 0; index < 5 && state.sparks.length < MAX_SPARKS; index += 1)
        state.sparks.push({
          x: x + (random(state) - 0.5) * 14,
          y: height - 16 - random(state) * 10,
          vx: (random(state) - 0.5) * 16,
          vy: -22 - random(state) * 34,
          life: 1,
          warm: random(state) < 0.5,
        });
  if (!state.sparks.length) return;
  ctx.globalCompositeOperation = "lighter";
  for (const spark of state.sparks) {
    spark.x += spark.vx * dt;
    spark.y += spark.vy * dt;
    spark.vy -= 6 * dt;
    spark.life -= dt / 1.3;
    ctx.globalAlpha = Math.max(0, spark.life) * 0.9;
    ctx.fillStyle = rgba(spark.warm ? theme.c : theme.a, 1);
    ctx.beginPath();
    ctx.arc(spark.x, spark.y, 1.2 + Math.max(0, spark.life) * 1.4, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
  state.sparks = state.sparks.filter((spark) => spark.life > 0 && spark.y > -4);
}

export const swarmSettled = (state: SwarmState) => state.sparks.length === 0 && state.jump === 0;
