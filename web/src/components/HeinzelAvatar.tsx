// One Heinzelmännchen as the app shows it, styled by its five choices (age, beard, hat and shirt colour, build), any
// accessories (umbrella, sunglasses, scarf: a data-only list) and showing its state: the whole figure by default (its page, the profile editor, the chat's header), or its bust for
// small slots up to 28 pixels (chat messages, lists, graph nodes). Both are the same drawing (heinzel.ts), drawn
// inline, so it needs no request. Motion is subtle and only without reduced motion: the badge breathes, a working
// one bobs.
import { cn } from "@/lib/utils";
import { useAvatarTheme } from "./AvatarTheme";
import {
  ACCESSORY_LABEL,
  type HeinzelLook,
  heinzelShapes,
  parseAccessories,
  type Shape,
  type View,
  viewBox,
} from "./heinzel";
import { type IdleMove, useIdleLife } from "./useIdleLife";

const STATE_LABEL = {
  idle: "",
  sleeping: "asleep",
  working: "working",
  thinking: "thinking",
  waiting: "waiting for you",
  blocked: "blocked",
  done: "done",
} as const;

type Props = HeinzelLook & {
  /** The whole figure, or head and shoulders for small slots. */
  view?: View;
  /** What a screen reader hears, e.g. the Heinzelmännchen's name; without it the picture is decoration. */
  label?: string;
  className?: string;
  /** Idle life: small moves at random moments (a blink, a hat tilt, a wave, a glance, a hop); the caller passes `playful`. */
  alive?: boolean;
};

/** Runs of shapes of one movable part share a group, so a cap tilts or a hand waves as one. */
function Parts({ shapes }: { shapes: Shape[] }) {
  const runs: Shape[][] = [];
  for (const shape of shapes) {
    const last = runs[runs.length - 1];
    if (last && shape.part && last[0]?.part === shape.part) last.push(shape);
    else runs.push([shape]);
  }
  return runs.map((run, index) => {
    const part = run[0]?.part;
    const inner = run.map((shape, i) => (
      // biome-ignore lint/suspicious/noArrayIndexKey: a fixed drawing, never reordered
      <Part key={i} shape={shape} />
    ));
    return part && part !== "badge" ? (
      // biome-ignore lint/suspicious/noArrayIndexKey: a fixed drawing, never reordered
      <g key={index} className={`hz-${part}`}>
        {inner}
      </g>
    ) : (
      // biome-ignore lint/suspicious/noArrayIndexKey: a fixed drawing, never reordered
      <g key={index}>{inner}</g>
    );
  });
}

function Part({ shape }: { shape: Shape }) {
  if (shape.kind === "group")
    return (
      <g transform={shape.transform}>
        <Parts shapes={shape.children} />
      </g>
    );
  if (shape.kind === "circle") return <circle cx={shape.cx} cy={shape.cy} r={shape.r} fill={shape.fill} />;
  return shape.stroke ? (
    <path
      d={shape.d}
      fill={shape.fill}
      stroke={shape.stroke}
      strokeWidth={shape.width}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ) : (
    <path d={shape.d} fill={shape.fill} />
  );
}

const HEAD_MOVES: readonly IdleMove[] = ["blink", "tilt", "glance-left", "glance-right", "hop"];

export function HeinzelAvatar({ view = "full", label, className, alive = false, ...look }: Props) {
  const theme = useAvatarTheme();
  const shapes = heinzelShapes({ ...look, theme: look.theme ?? theme });
  const state = look.state ?? "idle";
  const accessories = parseAccessories(look.accessories);
  // Accessories are decoration: without a label the picture stays hidden; with one they are part of what is said.
  const name = label
    ? [label, STATE_LABEL[state], ...accessories.map((accessory) => ACCESSORY_LABEL[accessory])]
        .filter(Boolean)
        .join(", ")
    : undefined;
  // Only a Heinzelmännchen at rest has idle moves; one with a state is busy with that.
  const move = useIdleLife(alive && (look.state ?? "idle") === "idle", view === "head" ? HEAD_MOVES : undefined);
  const body = shapes.filter((shape) => shape.part !== "badge");
  const badge = shapes.filter((shape) => shape.part === "badge");
  return (
    <svg
      viewBox={viewBox(look.age, view, accessories)}
      role={name ? "img" : undefined}
      aria-label={name}
      aria-hidden={name ? undefined : true}
      data-state={state}
      data-view={view}
      data-idle={move ?? undefined}
      className={cn(view !== "full" ? "size-[1.25em]" : "h-[2.1em] w-[1.25em]", "shrink-0 overflow-visible", className)}
    >
      <g className={state === "working" ? "motion-safe:animate-[hz-bob_1.6s_ease-in-out_infinite]" : undefined}>
        <Parts shapes={body} />
      </g>
      {badge.length > 0 && (
        <g className="motion-safe:animate-[hz-breathe_2.4s_ease-in-out_infinite]">
          <Parts shapes={badge} />
        </g>
      )}
    </svg>
  );
}
