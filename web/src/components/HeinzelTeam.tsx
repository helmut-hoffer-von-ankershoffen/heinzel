// A team of Heinzelmännchen as a line icon: one face under its cap in front, two caps peeking out behind it, in the
// same one-colour 2 px strokes on a 24 grid as HeinzelFace, so it sits among the navigation's icons. It stands where
// the app means several Heinzels (the Heinzels screen); one Heinzel, one voice, is HeinzelFace (docs/BRANDING.md).
import type { LucideIcon } from "lucide-react";
import { forwardRef } from "react";

export const HeinzelTeam = forwardRef<
  SVGSVGElement,
  React.ComponentPropsWithoutRef<"svg"> & { size?: number | string }
>(function HeinzelTeam({ size = 24, className, ...props }, ref) {
  return (
    <svg
      ref={ref}
      aria-hidden="true"
      data-testid="heinzel-team"
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      {...props}
    >
      {/* Two caps behind: left and right. */}
      <path d="M2.5 9.5 C3 7 4.5 5.5 7 4.5 C6.5 6 7.5 7.5 8.5 9.5" />
      <path d="M2 9.5 H6" />
      <path d="M15.5 9.5 C16.5 7.5 18 6 20.5 5 C20 6.5 21 8 22 9.5" />
      <path d="M18 9.5 H22" />
      {/* The one in front: cap, brim, face, eyes, smile. */}
      <path d="M8 12 C9 8.5 11 6.5 14 5.5 C13.5 7.5 15 9.5 17 12" />
      <path d="M7 12 H18" />
      <path d="M9 12 V14.5 A3.5 3.5 0 0 0 16 14.5 V12" />
      <path d="M11 14.5 H11.01 M14 14.5 H14.01" />
      <path d="M10.5 17.5 Q12.5 20 14.5 17.5" />
    </svg>
  );
}) as unknown as LucideIcon;
