// One Heinzelmännchen as the app shows it: next to its messages, in the chat's header, wherever someone talks to a
// particular one. Its look (tunic colour, age, beard) and its state come from the same drawing as the app's icon
// (heinzel.ts), drawn inline so it needs no request and stays sharp from 20 pixels up. Motion is subtle and only
// without reduced motion: the badge breathes, and a working one bobs.
import { cn } from "@/lib/utils";
import { type HeinzelLook, heinzelShapes, type Shape } from "./heinzel";

const STATE_LABEL = {
  idle: "",
  sleeping: "sleeping",
  working: "working",
  thinking: "thinking",
  waiting: "waiting for you",
  blocked: "blocked",
  done: "done",
} as const;

type Props = HeinzelLook & {
  /** What a screen reader hears, e.g. the Heinzelmännchen's name; without it the picture is decoration. */
  label?: string;
  className?: string;
};

function Part({ shape }: { shape: Shape }) {
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

export function HeinzelAvatar({ label, className, ...look }: Props) {
  const shapes = heinzelShapes(look);
  const state = look.state ?? "idle";
  const name = label ? [label, STATE_LABEL[state]].filter(Boolean).join(", ") : undefined;
  const body = shapes.filter((shape) => shape.part !== "badge");
  const badge = shapes.filter((shape) => shape.part === "badge");
  return (
    <svg
      viewBox="0 0 512 512"
      role={name ? "img" : undefined}
      aria-label={name}
      aria-hidden={name ? undefined : true}
      data-state={state}
      className={cn("size-[1.25em] shrink-0 overflow-visible", className)}
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
