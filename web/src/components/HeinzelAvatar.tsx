// One Heinzelmännchen as the app shows it, styled by its five choices (age, beard, hat and shirt colour, build) and
// showing its state: the whole figure by default (its page, the profile editor, the chat's header), or its bust for
// small slots up to 28 pixels (chat messages, lists, graph nodes). Both are the same drawing (heinzel.ts), drawn
// inline, so it needs no request. Motion is subtle and only without reduced motion: the badge breathes, a working
// one bobs.
import { cn } from "@/lib/utils";
import { type HeinzelLook, heinzelShapes, type Shape, type View, viewBox } from "./heinzel";

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
};

function Part({ shape }: { shape: Shape }) {
  if (shape.kind === "group")
    return (
      <g transform={shape.transform}>
        {shape.children.map((child, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: a fixed drawing, never reordered
          <Part key={index} shape={child} />
        ))}
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

export function HeinzelAvatar({ view = "full", label, className, ...look }: Props) {
  const shapes = heinzelShapes(look);
  const state = look.state ?? "idle";
  const name = label ? [label, STATE_LABEL[state]].filter(Boolean).join(", ") : undefined;
  const body = shapes.filter((shape) => shape.part !== "badge");
  const badge = shapes.filter((shape) => shape.part === "badge");
  return (
    <svg
      viewBox={viewBox(look.age, view)}
      role={name ? "img" : undefined}
      aria-label={name}
      aria-hidden={name ? undefined : true}
      data-state={state}
      data-view={view}
      className={cn(view !== "full" ? "size-[1.25em]" : "h-[2.1em] w-[1.25em]", "shrink-0 overflow-visible", className)}
    >
      <g className={state === "working" ? "motion-safe:animate-[hz-bob_1.6s_ease-in-out_infinite]" : undefined}>
        {body.map((shape, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: a fixed drawing, never reordered
          <Part key={index} shape={shape} />
        ))}
      </g>
      {badge.length > 0 && (
        <g className="motion-safe:animate-[hz-breathe_2.4s_ease-in-out_infinite]">
          {badge.map((shape, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: a fixed drawing, never reordered
            <Part key={index} shape={shape} />
          ))}
        </g>
      )}
    </svg>
  );
}
