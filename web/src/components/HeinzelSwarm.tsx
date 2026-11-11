// The product mark: the swarm of Heinzelmännchen walking together, the same drawing mark.svg and the site's logo are
// made from (heinzel.ts, `make icons`), drawn inline so it needs no request and paints before the theme list arrives.
// Square, sized by the caller (the sidebar's header shows it at 28 pixels); decoration, the name beside it speaks.
import { cn } from "@/lib/utils";
import { useAvatarTheme } from "./AvatarTheme";
import { Parts } from "./HeinzelAvatar";
import { heinzelShapes, SWARM } from "./heinzel";

export function HeinzelSwarm({ className }: { className?: string }) {
  const theme = useAvatarTheme();
  return (
    <svg
      viewBox="0 0 512 512"
      aria-hidden
      data-testid="heinzel-swarm"
      className={cn("size-7 shrink-0 overflow-visible", className)}
    >
      {SWARM.map(({ look, x, y, scale }, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: a fixed drawing, never reordered
        <g key={index} transform={`translate(${x} ${y}) scale(${scale})`}>
          <Parts shapes={heinzelShapes({ ...look, theme }).filter((shape) => shape.part !== "badge")} />
        </g>
      ))}
    </svg>
  );
}
