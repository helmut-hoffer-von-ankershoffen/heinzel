// A Heinzelmännchen's face under its pointed cap, drawn as a line icon: one colour (the text's), 2 px strokes on a 24
// grid, round caps, so it sits among the navigation's icons and reads at 20 px. It stands wherever the app used a
// robot for "a Heinzelmännchen". The coloured figure is HeinzelAvatar (heinzel.ts).
import type { LucideIcon } from "lucide-react";
import { forwardRef } from "react";

export const HeinzelFace = forwardRef<
  SVGSVGElement,
  React.ComponentPropsWithoutRef<"svg"> & { size?: number | string }
>(function HeinzelFace({ size = 24, className, ...props }, ref) {
  return (
    <svg
      ref={ref}
      aria-hidden="true"
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
      <path d="M6 10 C7 6 9.5 3.5 14 2 C13 4.5 15.5 7 18 10" />
      <path d="M5 10 H19" />
      <path d="M7.5 10 V13 A4.5 4.5 0 0 0 16.5 13 V10" />
      <path d="M10 13 H10.01 M14 13 H14.01" />
      <path d="M9 17 Q12 22 15 17" />
    </svg>
  );
}) as unknown as LucideIcon;
